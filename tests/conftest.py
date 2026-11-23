from datetime import date
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from backend.config import database_url
from backend.database import get_db
from backend.main import app
from backend.models import Base
from backend.routers import dashboard, finance, registry

AS_OF = date(2026, 11, 23)


@pytest.fixture(scope="session")
def db_url():
    return database_url()


@pytest.fixture
def engine(db_url):
    schema = f"qa_{uuid4().hex}"
    admin = create_engine(db_url, connect_args={"connect_timeout": 5})
    isolated = create_engine(
        db_url, isolation_level="READ COMMITTED",
        connect_args={"options": f"-c search_path={schema}", "connect_timeout": 5},
    )
    created = False
    try:
        with admin.begin() as connection:
            connection.execute(CreateSchema(schema))
        created = True
        Base.metadata.create_all(isolated)
        yield isolated
    finally:
        isolated.dispose()
        if created:
            with admin.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True))
        admin.dispose()


@pytest.fixture
def client(engine, monkeypatch):
    def session():
        with Session(engine, expire_on_commit=False) as db:
            yield db

    for module in (dashboard, finance, registry):
        monkeypatch.setattr(module, "local_today", lambda: AS_OF)
    app.dependency_overrides[get_db] = session
    # StaticFiles and endpoints run through ASGI. All writes land in the isolated schema.
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
