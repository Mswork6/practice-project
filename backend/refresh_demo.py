"""Replace a confirmed synthetic dataset atomically after verifying a full backup."""

import argparse
from datetime import datetime
from hashlib import file_digest, sha256
import json
import os
from pathlib import Path
import re
from subprocess import DEVNULL, run

from sqlalchemy import text
from sqlalchemy.orm import Session

from .config import LOCAL_ZONE, ROOT, database_url, local_today
from .database import get_engine
from .seed import TABLES, lock_dataset, populate
from .seed_audit import audit


def fingerprint(db):
    db.flush()
    digest = sha256()
    for model in TABLES:
        table = model.__tablename__
        digest.update(f"{table}\n".encode())
        for row in db.scalars(text(f"SELECT to_jsonb(t)::text FROM {table} t ORDER BY id")):
            digest.update((row + "\n").encode("utf-8"))
    return digest.hexdigest()


def verified_backup(pg_bin: Path):
    url = database_url()
    environment = {**os.environ, "PGPASSWORD": url.password, "PGCONNECT_TIMEOUT": "5", "PGCLIENTENCODING": "UTF8"}
    backup_dir = ROOT / "backups"
    backup_dir.mkdir(exist_ok=True)
    stamp = datetime.now(LOCAL_ZONE).strftime("%Y%m%d-%H%M%S-%f%z")
    destination = backup_dir / f"{url.database}-{stamp}.dump"
    partial = destination.with_suffix(".partial")
    run([
        str(pg_bin / "pg_dump.exe"), "--no-password", "--format=custom", "--file", str(partial),
        "--host", url.host, "--port", str(url.port), "--username", url.username, "--dbname", url.database,
    ], check=True, env=environment)
    if not partial.is_file() or partial.stat().st_size == 0:
        raise RuntimeError("Создан пустой архив резервной копии.")
    manifest = run([str(pg_bin / "pg_restore.exe"), "--list", str(partial)], check=True, capture_output=True)
    if not all(f"TABLE DATA public {model.__tablename__} ".encode() in manifest.stdout for model in TABLES):
        raise RuntimeError("В архиве отсутствуют данные одной или нескольких таблиц public.")
    run([str(pg_bin / "pg_restore.exe"), "--file=-", str(partial)], check=True, stdout=DEVNULL)
    partial.replace(destination)
    with destination.open("rb") as file:
        checksum = file_digest(file, "sha256").hexdigest()
    destination.with_suffix(".sha256").write_text(f"{checksum}  {destination.name}\n", encoding="ascii")
    print(f"Резервная копия проверена: {destination} ({destination.stat().st_size} байт)", flush=True)
    return destination


def replace_synthetic(db, expected_digest, backup, today):
    lock_dataset(db)
    if fingerprint(db) != expected_digest:
        raise ValueError("Отпечаток изменился. Замена отменена до удаления записей.")
    destination = backup()
    for model in reversed(TABLES):
        db.execute(model.__table__.delete())
    if populate(db, today) is None:
        raise RuntimeError("Не удалось заполнить очищенные таблицы.")
    return destination, audit(db, today)


def main():
    parser = argparse.ArgumentParser(description="Проверить отпечаток или заменить подтверждённый синтетический набор public.")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--fingerprint", action="store_true", help="Вывести отпечаток текущих данных public.")
    action.add_argument("--replace-synthetic", metavar="SHA256", help="Подтвердить замену всего синтетического набора public.")
    default_bin = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "PostgreSQL" / "15" / "bin"
    parser.add_argument("--pg-bin", type=Path, default=default_bin)
    args = parser.parse_args()

    if args.replace_synthetic and not re.fullmatch(r"[0-9a-f]{64}", args.replace_synthetic):
        parser.error("Укажите проверенный SHA-256 текущего набора.")
    with Session(get_engine()) as db, db.begin():
        if args.fingerprint:
            db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            db.execute(text("SET LOCAL search_path TO public"))
            print(fingerprint(db))
            return
        db.execute(text("SET LOCAL search_path TO public"))
        db.execute(text("SET LOCAL lock_timeout TO '10s'"))
        print("Замена всех строк пяти таблиц public подтверждена. Начинается резервное копирование.", flush=True)
        destination, report = replace_synthetic(db, args.replace_synthetic, lambda: verified_backup(args.pg_bin), local_today())

    print(json.dumps({"backup": str(destination), **report}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
