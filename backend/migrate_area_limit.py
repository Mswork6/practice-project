"""Audit existing property areas and install the upper-bound CHECK transactionally."""

import argparse
import json
from pathlib import Path
from subprocess import CalledProcessError

from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

from .database import get_engine
from .refresh_demo import default_pg_bin, verified_backup
from .services.limits import AREA_CHECK, AREA_CHECK_SQL, MAX_AREA

# PostgreSQL renders the integer limit as a numeric literal.
EXPECTED_CHECK = f"{AREA_CHECK_SQL}::numeric"


def plan(connection):
    areas = connection.execute(text(
        "SELECT id, area FROM properties WHERE area IS NOT NULL AND (area <= 0 OR area > :maximum) ORDER BY id"
    ), {"maximum": MAX_AREA}).all()
    checks = {item["name"]: item["sqltext"] for item in inspect(connection).get_check_constraints("properties")}
    state = "current" if checks.get(AREA_CHECK) == EXPECTED_CHECK else ("outdated" if AREA_CHECK in checks else "missing")
    return {
        "total": connection.scalar(text("SELECT count(*) FROM properties")),
        "invalid_areas": [{"id": row.id, "area": str(row.area)} for row in areas],
        "area_check": state, "property_checks": sorted(checks),
    }


def migrate(connection, backup):
    # Block writers while still allowing pg_dump to take its read lock on the table.
    connection.execute(text("SET LOCAL lock_timeout TO '10s'"))
    connection.execute(text("LOCK TABLE properties IN SHARE ROW EXCLUSIVE MODE"))
    report = plan(connection)
    if report["invalid_areas"]:
        raise ValueError("Площадь некоторых объектов вне допустимого диапазона. Применение остановлено. Запустите --audit.")
    if report["area_check"] == "current":
        return {**report, "status": "current"}

    destination = backup()
    if report["area_check"] == "outdated":
        connection.execute(text(f"ALTER TABLE properties DROP CONSTRAINT {AREA_CHECK}"))
    connection.execute(text(f"ALTER TABLE properties ADD CONSTRAINT {AREA_CHECK} CHECK ({AREA_CHECK_SQL})"))
    return {**plan(connection), "status": "migrated", "backup": str(destination)}


def main():
    parser = argparse.ArgumentParser(description="Проверить площади объектов и ограничение площади в public.")
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
            report = plan(connection) if args.audit else migrate(connection, lambda: verified_backup(args.pg_bin))
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
