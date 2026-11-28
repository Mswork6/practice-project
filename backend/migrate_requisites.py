"""Audit existing identifiers and install cadastral constraints transactionally."""

import argparse
from collections import Counter
import json
from pathlib import Path
from subprocess import CalledProcessError

from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

from .database import get_engine
from .refresh_demo import default_pg_bin, verified_backup
from .seed import legacy_individual_tax_number, make_parties
from .services.counterparty import normalize_tax_id, valid_tax_checksum, valid_tax_id
from .services.requisites import CADASTRAL_PATTERN, normalize_cadastral, valid_cadastral

UNIQUE = "unique_property_cadastral_number"
CHECK = "valid_property_cadastral_number"


def plan(connection):
    demos = {
        (party.name, legacy_individual_tax_number(index), party.email): party.tax_id
        for index, party in enumerate(make_parties(), 1) if party.name.startswith("ИП ")
    }
    parties = connection.execute(text("SELECT id, name, tax_id, email FROM counterparties ORDER BY id")).all()
    properties = connection.execute(text("SELECT id, cadastral_number FROM properties ORDER BY id")).all()
    current_tax = Counter(row.tax_id for row in parties if row.tax_id)
    final_tax, cadastral_counts = Counter(), Counter()
    tax_updates, cadastral_updates = [], []
    tax = dict(total=len(parties), missing=0, invalid_format=0, all_zero=0, invalid_checksum=0,
               duplicate_groups=sum(count > 1 for count in current_tax.values()), blocked_ids=[])
    cadastral = dict(total=len(properties), filled=0, null=0, blank=0, surrounding_whitespace=0, invalid_ids=[])
    for row in parties:
        value = normalize_tax_id(row.tax_id)
        tax["missing"] += not value
        tax["invalid_format"] += bool(value) and not valid_tax_id(row.tax_id)
        tax["all_zero"] += bool(value) and not value.strip("0")
        tax["invalid_checksum"] += valid_tax_id(value) and bool(value.strip("0")) and not valid_tax_checksum(value)
        target = row.tax_id
        if not valid_tax_checksum(target):
            if demo := demos.get((row.name, row.tax_id, row.email)):
                target = demo
                tax_updates.append({"id": row.id, "value": target})
            else:
                tax["blocked_ids"].append(row.id)
        if target:
            final_tax[target] += 1

    tax["target_conflict_groups"] = sum(count > 1 for count in final_tax.values())
    tax["occupied_replacement_ids"] = [row["id"] for row in tax_updates if row["value"] in current_tax]
    tax["confirmed_demo_corrections"] = len(tax_updates)
    for row in properties:
        original = row.cadastral_number
        value = normalize_cadastral(original)
        cadastral["null"] += original is None
        cadastral["blank"] += original is not None and value is None
        cadastral["filled"] += value is not None
        cadastral["surrounding_whitespace"] += original is not None and original != original.strip()
        if value is not None:
            cadastral_counts[value] += 1
            if not valid_cadastral(value):
                cadastral["invalid_ids"].append(row.id)
        if value != original:
            cadastral_updates.append({"id": row.id, "value": value})

    cadastral["duplicate_groups"] = sum(count > 1 for count in cadastral_counts.values())
    cadastral["duplicate_ids"] = [
        row.id for row in properties if cadastral_counts[normalize_cadastral(row.cadastral_number)] > 1
    ]
    cadastral["normalizations"] = len(cadastral_updates)
    metadata = inspect(connection)
    unique = {item["name"] for item in metadata.get_unique_constraints("properties")
              if item["column_names"] == ["cadastral_number"]}
    checks = {item["name"] for item in metadata.get_check_constraints("properties")}
    installed = UNIQUE in unique and CHECK in checks
    report = {"tax_id": tax, "cadastral_number": cadastral, "constraints_installed": installed}
    return report, tax_updates, cadastral_updates, UNIQUE in unique, CHECK in checks


def migrate(connection, backup):
    connection.execute(text("SET LOCAL lock_timeout TO '10s'"))
    connection.execute(text("LOCK TABLE counterparties, properties IN SHARE ROW EXCLUSIVE MODE"))
    report, tax_updates, cadastral_updates, has_unique, has_check = plan(connection)
    tax, cadastral = report["tax_id"], report["cadastral_number"]
    if (tax["blocked_ids"] or tax["target_conflict_groups"] or tax["occupied_replacement_ids"]
            or cadastral["invalid_ids"] or cadastral["duplicate_groups"]):
        raise ValueError("Выявлены неподтверждённые или конфликтующие реквизиты. Применение остановлено. Запустите --audit.")
    if report["constraints_installed"] and not tax_updates and not cadastral_updates:
        return {**report, "status": "current"}

    destination = backup()
    if tax_updates:
        connection.execute(text("UPDATE counterparties SET tax_id = :value WHERE id = :id"), tax_updates)
    if cadastral_updates:
        connection.execute(text("UPDATE properties SET cadastral_number = :value WHERE id = :id"), cadastral_updates)
    if not has_unique:
        connection.execute(text(f"ALTER TABLE properties ADD CONSTRAINT {UNIQUE} UNIQUE (cadastral_number)"))
    if not has_check:
        connection.execute(text(
            f"ALTER TABLE properties ADD CONSTRAINT {CHECK} CHECK (cadastral_number ~ '{CADASTRAL_PATTERN}')"
        ))
    return {**report, "status": "migrated", "constraints_installed": True, "backup": str(destination)}


def main():
    parser = argparse.ArgumentParser(description="Проверить ИНН и кадастровые номера в public.")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--audit", action="store_true")
    action.add_argument("--apply", action="store_true")
    parser.add_argument("--pg-bin", type=Path, default=default_pg_bin())
    args = parser.parse_args()
    try:
        with get_engine().begin() as connection:
            if args.audit:
                connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            connection.execute(text("SET LOCAL search_path TO public"))
            report = plan(connection)[0] if args.audit else migrate(connection, lambda: verified_backup(args.pg_bin))
    except (ValueError, RuntimeError) as error:
        parser.exit(1, f"{error}\n")
    except (OSError, CalledProcessError):
        parser.exit(1, "Резервная копия не прошла проверку. Миграция отменена.\n")
    except SQLAlchemyError as error:
        state = getattr(getattr(error, "orig", None), "sqlstate", None)
        parser.exit(1, f"Ошибка PostgreSQL. Транзакция отменена (SQLSTATE: {state or 'недоступен'}).\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
