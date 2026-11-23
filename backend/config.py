import os
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from sqlalchemy import URL

ROOT = Path(__file__).resolve().parents[1]
PROJECT_DATABASE = "municipal_property"
LOCAL_ZONE = ZoneInfo("Asia/Yekaterinburg")

load_dotenv(ROOT / ".env", encoding="utf-8-sig")


def local_today() -> date:
    return datetime.now(LOCAL_ZONE).date()


def database_url() -> URL:
    name = os.getenv("DB_NAME", PROJECT_DATABASE)
    if name != PROJECT_DATABASE:
        raise RuntimeError(f"DB_NAME должен быть {PROJECT_DATABASE}.")
    password = os.getenv("DB_PASSWORD")
    if not password:
        raise RuntimeError("Укажите DB_PASSWORD в локальном файле .env.")
    return URL.create(
        "postgresql+psycopg",
        username=os.getenv("DB_USER", "municipal_user"),
        password=password,
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "5432")),
        database=name,
    )
