"""Audit or upgrade the counterparty table in the existing project database."""

import argparse
from collections import Counter
import json
from pathlib import Path
from subprocess import CalledProcessError

from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

from .database import get_engine
from .refresh_demo import default_pg_bin, verified_backup
from .seed import make_parties, tax_number
from .services.counterparty import clean_email, normalize_tax_id, valid_tax_id


def plan(connection):
    metadata = inspect(connection)
    columns = {column["name"]: column for column in metadata.get_columns("counterparties")}
    if "email" in columns and "contact" not in columns:
        unique = metadata.get_unique_constraints("counterparties")
        checks = metadata.get_check_constraints("counterparties")
        if (
            "tax_id" not in columns or columns["tax_id"]["nullable"]
            or not any(item["name"] == "unique_counterparty_tax_id" and item["column_names"] == ["tax_id"] for item in unique)
            or not any(item["name"] == "valid_counterparty_tax_id" for item in checks)
        ):
            raise ValueError("В обновлённой таблице counterparties отсутствуют ограничения ИНН.")
        total = connection.scalar(text("SELECT count(*) FROM counterparties"))
        return {"version": "current", "total": total}, []
    if "contact" not in columns or "email" in columns:
        raise ValueError("Структура counterparties отличается от исходной и целевой схем.")

    rows = connection.execute(text("SELECT id, name, tax_id, contact FROM counterparties ORDER BY id")).all()
    demos = list(enumerate(make_parties(), 1))
    fixes = {
        (party.name, tax_number(index, True, individual_width=11), party.email): party.tax_id
        for index, party in demos if party.name.startswith("ИП ")
    }
    report = {
        "version": "legacy", "total": len(rows), "missing_tax_ids": 0, "invalid_tax_id_lengths": 0,
        "invalid_tax_id_characters": 0, "duplicate_normalized_groups": 0, "duplicate_final_groups": 0,
        "normalized_tax_ids": 0, "corrected_demo_tax_ids": 0, "blocked_ids": [],
        "blank_contacts": 0, "valid_emails": 0, "trimmed_emails": 0, "unusable_contacts": 0,
    }
    normalized_ids = Counter()
    target_ids = Counter()
    updates = []
    for row in rows:
        tax_id = normalize_tax_id(row.tax_id)
        if not tax_id:
            report["missing_tax_ids"] += 1
        else:
            normalized_ids[tax_id] += 1
            report["invalid_tax_id_lengths"] += len(tax_id) not in (10, 12)
            report["invalid_tax_id_characters"] += not tax_id.isascii() or not tax_id.isdigit()
        if not valid_tax_id(tax_id):
            tax_id = fixes.get((row.name, row.tax_id, row.contact))
            if tax_id is None:
                report["blocked_ids"].append(row.id)
            else:
                report["corrected_demo_tax_ids"] += 1
        elif tax_id != row.tax_id:
            report["normalized_tax_ids"] += 1
        if tax_id:
            target_ids[tax_id] += 1
        try:
            email = clean_email(row.contact)
        except ValueError:
            email = None
            report["unusable_contacts"] += 1
        else:
            report["blank_contacts" if email is None else "valid_emails"] += 1
            report["trimmed_emails"] += email is not None and email != row.contact
        if tax_id != row.tax_id or email != row.contact:
            updates.append({"id": row.id, "tax_id": tax_id, "email": email})

    report["duplicate_normalized_groups"] = sum(count > 1 for count in normalized_ids.values())
    report["duplicate_final_groups"] = sum(count > 1 for count in target_ids.values())
    return report, updates


def migrate(connection, backup):
    # Block writers while still allowing pg_dump to take its read lock on the old table.
    connection.execute(text("SET LOCAL lock_timeout TO '10s'"))
    connection.execute(text("LOCK TABLE counterparties IN SHARE ROW EXCLUSIVE MODE"))
    report, updates = plan(connection)
    if report["version"] == "current":
        return report
    if report["blocked_ids"] or report["duplicate_final_groups"]:
        raise ValueError("Есть неразрешённые ИНН. Запустите аудит, чтобы определить объём ручной проверки.")
    destination = backup()
    connection.execute(text("ALTER TABLE counterparties RENAME COLUMN contact TO email"))
    if updates:
        connection.execute(text("UPDATE counterparties SET tax_id = :tax_id, email = :email WHERE id = :id"), updates)
    connection.execute(text("""
        ALTER TABLE counterparties
        ALTER COLUMN tax_id SET NOT NULL,
        ADD CONSTRAINT unique_counterparty_tax_id UNIQUE (tax_id),
        ADD CONSTRAINT valid_counterparty_tax_id CHECK (tax_id ~ '^([0-9]{10}|[0-9]{12})$')
    """))
    return {**report, "version": "migrated", "backup": str(destination)}


def main():
    parser = argparse.ArgumentParser(description="Проверить или обновить таблицу контрагентов в public.")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--audit", action="store_true", help="Проверить данные без записи.")
    action.add_argument("--apply", action="store_true", help="Проверить резервную копию и выполнить миграцию.")
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
