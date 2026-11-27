from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend import refresh_demo
from backend.models import Property
from backend.seed import populate
from backend.seed_audit import audit
from conftest import AS_OF


@pytest.fixture
def old_digest(engine):
    with Session(engine) as db, db.begin():
        db.add(Property(name="Объект для проверки", type="land", address="Адрес для проверки", status="available"))
        db.flush()
        return refresh_demo.fingerprint(db)


def test_full_seed_audit(engine):
    with Session(engine) as db, db.begin():
        counts = populate(db, AS_OF)
        digest = refresh_demo.fingerprint(db)
        report = audit(db, AS_OF)
        assert list(report["counts"].values()) == list(counts)
        assert 310 < report["counts"]["accruals"] < 500
        assert all(report["term_buckets"])
    with Session(engine) as db:
        assert refresh_demo.fingerprint(db) == digest


def test_replace_stops_if_data_changed(engine, old_digest):
    with Session(engine) as db, db.begin():
        db.scalar(select(Property)).note = "Изменено после проверки"
    with Session(engine) as db, db.begin(), pytest.raises(ValueError, match="Отпечаток изменился"):
        refresh_demo.replace_synthetic(db, old_digest, lambda: pytest.fail("Unexpected backup"), AS_OF)
    with Session(engine) as db:
        assert db.scalar(select(Property)).note == "Изменено после проверки"


def test_replace_preserves_data_if_backup_fails(engine, old_digest):
    def failed_backup():
        raise OSError("backup failed")

    with pytest.raises(OSError), Session(engine) as db, db.begin():
        refresh_demo.replace_synthetic(db, old_digest, failed_backup, AS_OF)
    with Session(engine) as db:
        assert refresh_demo.fingerprint(db) == old_digest


def test_replace_rolls_back_if_audit_fails(engine, old_digest, monkeypatch):
    def failed_audit(db, today):
        assert db.scalar(select(func.count()).select_from(Property)) == 100
        raise ValueError("audit failed")

    monkeypatch.setattr(refresh_demo, "audit", failed_audit)
    with pytest.raises(ValueError, match="audit failed"), Session(engine) as db, db.begin():
        refresh_demo.replace_synthetic(db, old_digest, lambda: Path("verified.dump"), AS_OF)
    with Session(engine) as db:
        assert refresh_demo.fingerprint(db) == old_digest
