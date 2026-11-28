from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from backend.migrate_area_limit import EXPECTED_CHECK, migrate, plan
from backend.routers import registry
from backend.seed import TABLES
from backend.services.limits import AREA_CHECK, MAX_AREA
from conftest import AS_OF
from test_application import PROPERTY, create, create_agreement, make_accrual
from test_business_rules import day


def fields(response):
    payload = response.json()
    return {error["loc"][-1]: error["msg"] for error in payload.get("errors", payload["detail"])}


SOON = AS_OF.replace(year=AS_OF.year + 10)
DISTANT = AS_OF.replace(year=AS_OF.year + 100)
AREA_MESSAGE = "Площадь объекта не может превышать 1 000 000 000 м²."
INSERT_AREA = text("""
    INSERT INTO properties (name, type, address, area, status, created_at)
    VALUES ('A', 'land', 'B', :area, 'available', CURRENT_DATE)
""")


def test_area_boundaries_on_create_and_edit(client):
    assert create(client, "properties", PROPERTY)["area"] is None
    prop = create(client, "properties", {**PROPERTY, "area": "1000000000,00"})
    assert prop["area"] == "1000000000.00"
    url = f"/api/properties/{prop['id']}"
    assert client.patch(url, json={"area": "0.01"}).json()["area"] == "0.01"
    assert client.patch(url, json={"area": None}).json()["area"] is None
    assert Decimal(client.patch(url, json={"area": "1000000000"}).json()["area"]) == MAX_AREA
    response = client.patch(url, json={"area": "1000000000.01"})
    assert response.status_code == 422 and fields(response)["area"] == AREA_MESSAGE
    assert Decimal(client.get(url).json()["area"]) == MAX_AREA


@pytest.mark.parametrize("value", ["1000000000.01", "1000000000,01", "1000000000000.00", "1e30"])
def test_area_above_limit_uses_limit_message(client, value):
    response = client.post("/api/properties", json={**PROPERTY, "area": value})
    assert response.status_code == 422 and fields(response)["area"] == AREA_MESSAGE


def test_money_range_is_not_narrowed_by_the_area_limit(client):
    agreement, body = create_agreement(client, amount="999999999999,99")
    assert agreement["amount"] == "999999999999.99"
    accrual = make_accrual(client, agreement["id"], amount="999999999999.99")
    assert accrual["amount"] == "999999999999.99"
    response = client.post("/api/contracts", json={**body, "number": "ШИРИНА", "amount": "1000000000000"})
    assert response.status_code == 422 and fields(response)["amount"] == "Допустимо не более 12 цифр до десятичного разделителя."


def test_postgresql_rejects_area_above_limit_and_accepts_null(engine):
    with pytest.raises(IntegrityError) as error, engine.begin() as connection:
        connection.execute(INSERT_AREA, {"area": MAX_AREA + Decimal("0.01")})
    assert error.value.orig.sqlstate == "23514" and error.value.orig.diag.constraint_name == AREA_CHECK
    with pytest.raises(IntegrityError) as error, engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO properties (name, type, address, area, status, created_at)
            VALUES ('N', 'land', 'B', 'NaN', 'available', CURRENT_DATE)
        """))
    assert error.value.orig.sqlstate == "23514" and error.value.orig.diag.constraint_name == AREA_CHECK
    with engine.begin() as connection:
        connection.execute(INSERT_AREA, {"area": None})
        connection.execute(INSERT_AREA, {"area": MAX_AREA})
        assert connection.scalar(text("SELECT count(*) FROM properties WHERE area IS NOT NULL")) == 1


def test_contract_date_bounds(client):
    historic, _ = create_agreement(client, number="ИСТОРИЧЕСКИЙ", start_date="1900-01-01", end_date="1900-01-02")
    assert historic["state"] == "expired"
    planned, _ = create_agreement(client, number="ПЛАН", start_date=SOON.isoformat(), end_date=DISTANT.isoformat())
    assert planned["state"] == "pending"
    assert create_agreement(client, number="ПРОДАЖА", type="sale", end_date=None)[0]["state"] == "active"
    _, base = create_agreement(client, number="БАЗА")
    for patch, field, message in [
        ({"start_date": "1899-12-31"}, "start_date", "Дата начала договора не может быть ранее 01.01.1900."),
        ({"start_date": (SOON + timedelta(days=1)).isoformat(), "end_date": DISTANT.isoformat()}, "start_date",
         f"Дата начала договора не может быть позднее {SOON:%d.%m.%Y}."),
        ({"start_date": "3047-01-01", "end_date": "3048-01-01"}, "start_date",
         f"Дата начала договора не может быть позднее {SOON:%d.%m.%Y}."),
        ({"end_date": (DISTANT + timedelta(days=1)).isoformat()}, "end_date",
         f"Дата окончания договора не может быть позднее {DISTANT:%d.%m.%Y}."),
        ({"end_date": "2025-12-31"}, "end_date", "Дата окончания не может быть раньше даты начала."),
        ({"start_date": "2027-02-29"}, "start_date", "Укажите корректную календарную дату."),
    ]:
        response = client.post("/api/contracts", json={**base, "number": "ГРАНИЦА", **patch})
        assert response.status_code == 422 and fields(response)[field] == message
    response = client.patch(f"/api/contracts/{planned['id']}", json={"start_date": (SOON + timedelta(days=1)).isoformat()})
    assert response.status_code == 422 and fields(response)["start_date"] == f"Дата начала договора не может быть позднее {SOON:%d.%m.%Y}."


def test_accrual_date_bounds_on_create_and_edit(client):
    agreement, _ = create_agreement(client)
    make_accrual(client, agreement["id"], accrual_date=AS_OF.isoformat(), due_date=DISTANT.isoformat())
    for patch, field, message in [
        ({"accrual_date": "1899-12-31"}, "accrual_date", "Дата начисления не может быть ранее 01.01.1900."),
        ({"accrual_date": (SOON + timedelta(days=1)).isoformat()}, "accrual_date",
         f"Дата начисления не может быть позднее {SOON:%d.%m.%Y}."),
        ({"due_date": (DISTANT + timedelta(days=1)).isoformat()}, "due_date",
         f"Срок оплаты не может быть позднее {DISTANT:%d.%m.%Y}."),
        ({"due_date": "2026-01-01"}, "due_date", "Срок оплаты не может предшествовать дате начисления."),
    ]:
        response = client.post("/api/accruals", json={
            "contract_id": agreement["id"], "amount": "10", "accrual_date": AS_OF.isoformat(),
            "due_date": DISTANT.isoformat(), **patch,
        })
        assert response.status_code == 422 and fields(response)[field] == message
    accrual = make_accrual(client, agreement["id"])
    response = client.patch(f"/api/accruals/{accrual['id']}", json={"accrual_date": "3047-01-01", "due_date": "3047-01-02"})
    assert response.status_code == 422 and fields(response)["accrual_date"] == f"Дата начисления не может быть позднее {SOON:%d.%m.%Y}."
    response = client.post("/api/payments", json={"accrual_id": accrual["id"], "amount": "10", "payment_date": day(1)})
    assert response.status_code == 422 and fields(response)["payment_date"] == f"Дата платежа не может быть позднее {AS_OF:%d.%m.%Y}."


def test_extension_and_successor_follow_date_bounds(client):
    agreement, body = create_agreement(client, number="АКТУАЛЬНЫЙ")
    url = f"/api/contracts/{agreement['id']}"
    assert client.post(f"{url}/extend", json={"end_date": DISTANT.isoformat()}).status_code == 200
    response = client.post(f"{url}/extend", json={"end_date": (DISTANT + timedelta(days=1)).isoformat()})
    assert response.status_code == 422 and fields(response)["end_date"] == f"Дата окончания договора не может быть позднее {DISTANT:%d.%m.%Y}."
    old, _ = create_agreement(client, number="СТАРЫЙ", start_date=day(-60), end_date=day(-10))
    later = (SOON + timedelta(days=1)).isoformat()
    response = client.post(f"/api/contracts/{old['id']}/successor", json={
        **body, "number": "НОВЫЙ", "start_date": later, "end_date": (SOON + timedelta(days=2)).isoformat(),
    })
    assert response.status_code == 422 and fields(response)["start_date"] == f"Дата начала договора не может быть позднее {SOON:%d.%m.%Y}."


def test_date_bounds_add_calendar_years_to_the_application_date(client, monkeypatch):
    monkeypatch.setattr(registry, "local_today", lambda: date(2028, 2, 29))
    _, base = create_agreement(client, number="БАЗА")
    # 29.02.2028 + 10 years = 28.02.2038, + 100 years = 29.02.2128; both bounds are inclusive.
    assert create_agreement(client, number="ГРАНИЦА", start_date="2038-02-28", end_date="2128-02-29")[0]["state"] == "pending"
    for number, patch, field, message in [
        ("СТАРТ", {"start_date": "2038-03-01", "end_date": "2039-01-01"}, "start_date",
         "Дата начала договора не может быть позднее 28.02.2038."),
        ("ФИНАЛ", {"start_date": "2029-02-28", "end_date": "2128-03-01"}, "end_date",
         "Дата окончания договора не может быть позднее 29.02.2128."),
    ]:
        response = client.post("/api/contracts", json={**base, "number": number, **patch})
        assert response.status_code == 422 and fields(response)[field] == message


def test_active_contract_term_rules_by_type(client):
    rental, _ = create_agreement(client, number="АРЕНДА")
    url = f"/api/contracts/{rental['id']}"
    response = client.patch(url, json={"end_date": day(60)})
    assert response.status_code == 409 and response.json()["detail"] == "Увеличить срок действующей аренды можно через действие «Продлить»."
    # Reject a first step that could otherwise bypass the cap in a later request.
    for kind in ("sale", "other"):
        response = client.patch(url, json={"type": kind})
        assert response.status_code == 409 and "type" in fields(response)
        assert client.patch(url, json={"type": kind, "end_date": day(90)}).status_code == 409
    assert client.get(url).json()["type"] == "real_estate_rent"
    assert client.post(f"{url}/extend", json={"end_date": day(60)}).json()["end_date"] == day(60)

    for kind in ("sale", "other"):
        contract, _ = create_agreement(client, type=kind, number=f"СРОК-{kind}")
        url = f"/api/contracts/{contract['id']}"
        result = client.patch(url, json={"end_date": day(60)})
        assert result.status_code == 200
        assert (result.json()["end_date"], result.json()["state"]) == (day(60), "active")
        # A sale keeps an existing end date by the lifecycle rule; any other type requires the field.
        response = client.patch(url, json={"end_date": None})
        assert response.status_code == (409 if kind == "sale" else 422)
        assert client.patch(url, json={"end_date": day(-1)}).status_code == 409
    # The submitted type is checked too, so a temporary switch cannot extend a rental term.
    other, _ = create_agreement(client, type="other", number="ТИП")
    url = f"/api/contracts/{other['id']}"
    assert client.patch(url, json={"type": "real_estate_rent", "end_date": day(90)}).status_code == 409
    assert client.patch(url, json={"type": "real_estate_rent"}).json()["type"] == "real_estate_rent"
    assert client.patch(url, json={"end_date": day(21)}).status_code == 409


def test_payment_date_bounds_on_create_and_edit(client):
    agreement, _ = create_agreement(client)
    accrual = make_accrual(client, agreement["id"], accrual_date="1900-01-01", due_date="1900-01-01")
    body = {"accrual_id": accrual["id"], "amount": "10"}
    payment = create(client, "payments", {**body, "payment_date": "1900-01-01"})
    url = f"/api/payments/{payment['id']}"
    assert client.patch(url, json={"payment_date": AS_OF.isoformat()}).status_code == 200
    for day_to_check, message in [
        ("1899-12-31", "Дата платежа не может быть ранее 01.01.1900."),
        (day(1), f"Дата платежа не может быть позднее {AS_OF:%d.%m.%Y}."),
    ]:
        post = client.post("/api/payments", json={**body, "payment_date": day_to_check})
        patch = client.patch(url, json={"payment_date": day_to_check})
        assert all(response.status_code == 422 and fields(response)["payment_date"] == message for response in (post, patch))


def test_deletion_of_records_without_relations(client):
    prop = create(client, "properties", PROPERTY)
    party = create(client, "counterparties", {"name": "Свободный", "tax_id": "0012345673"})
    assert client.delete(f"/api/properties/{prop['id']}").status_code == 204
    assert client.delete(f"/api/counterparties/{party['id']}").status_code == 204
    agreement, _ = create_agreement(client)
    accrual = make_accrual(client, agreement["id"], amount="100")
    payment = create(client, "payments", {"accrual_id": accrual["id"], "amount": "25", "payment_date": day()})
    assert client.delete(f"/api/payments/{payment['id']}").status_code == 204
    assert client.delete(f"/api/accruals/{accrual['id']}").status_code == 204
    assert client.delete(f"/api/contracts/{agreement['id']}").status_code == 204


def test_deletion_is_blocked_by_related_records_of_any_status(client):
    agreement, body = create_agreement(client)
    accrual = make_accrual(client, agreement["id"], amount="100")
    payment = create(client, "payments", {"accrual_id": accrual["id"], "amount": "25", "payment_date": day()})
    assert client.delete(f"/api/accruals/{accrual['id']}").status_code == 409
    assert client.delete(f"/api/contracts/{agreement['id']}").status_code == 409
    assert client.delete(f"/api/properties/{body['property_id']}").status_code == 409
    assert client.delete(f"/api/counterparties/{body['counterparty_id']}").status_code == 409
    # A closed contract still protects its parents and keeps the payment history.
    assert client.post(f"/api/contracts/{agreement['id']}/complete").status_code == 200
    assert client.delete(f"/api/properties/{body['property_id']}").status_code == 409
    assert client.delete(f"/api/counterparties/{body['counterparty_id']}").status_code == 409
    assert client.get(f"/api/payments/{payment['id']}").status_code == 200
    assert Decimal(client.get(f"/api/accruals/{accrual['id']}").json()["paid"]) == 25


def test_api_rejects_deletion_after_a_related_record_appears(client):
    prop = create(client, "properties", PROPERTY)
    party = create(client, "counterparties", {"name": "Владелец", "tax_id": "0012345673"})
    agreement, _ = create_agreement(client, property_id=prop["id"], counterparty_id=party["id"])
    assert client.delete(f"/api/properties/{prop['id']}").status_code == 409
    assert client.delete(f"/api/counterparties/{party['id']}").status_code == 409
    assert client.get(f"/api/contracts/{agreement['id']}").status_code == 200


def legacy_schema(connection, definition=None):
    connection.execute(text(f"ALTER TABLE properties DROP CONSTRAINT {AREA_CHECK}"))
    if definition:
        connection.execute(text(f"ALTER TABLE properties ADD CONSTRAINT {AREA_CHECK} CHECK ({definition})"))


def constraint_oids(connection):
    rows = connection.execute(text(
        "SELECT conname, oid FROM pg_constraint WHERE conrelid = 'properties'::regclass"
    )).mappings()
    return {row["conname"]: row["oid"] for row in rows}


def snapshot(connection):
    return [connection.scalars(text(f"SELECT to_jsonb(t) FROM {model.__tablename__} t ORDER BY id")).all()
            for model in TABLES]


def test_area_migration_installs_check_and_keeps_other_constraints(engine):
    with engine.begin() as connection:
        legacy_schema(connection)
        before = constraint_oids(connection)
    with engine.begin() as connection:
        initial = plan(connection)
        assert initial["area_check"] == "missing"
        result = migrate(connection, lambda: "verified.dump")
        assert result["status"] == "migrated"
        assert result["property_checks"] == sorted([*initial["property_checks"], AREA_CHECK])
        assert plan(connection)["area_check"] == "current"
    with engine.begin() as connection:
        after = constraint_oids(connection)
        assert after.pop(AREA_CHECK)
        assert after == before
        assert migrate(connection, lambda: pytest.fail("Unexpected backup"))["status"] == "current"
        definition = next(item["sqltext"] for item in inspect(connection).get_check_constraints("properties")
                         if item["name"] == AREA_CHECK)
        assert definition == EXPECTED_CHECK


def test_area_migration_replaces_an_outdated_check(engine):
    with engine.begin() as connection:
        legacy_schema(connection, "area <= 5000000000")
    with engine.begin() as connection:
        assert plan(connection)["area_check"] == "outdated"
        assert migrate(connection, lambda: "verified.dump")["status"] == "migrated"
        assert plan(connection)["area_check"] == "current"


def test_area_migration_stops_on_incompatible_area_before_backup(engine):
    with engine.begin() as connection:
        legacy_schema(connection)
        connection.execute(INSERT_AREA, {"area": MAX_AREA + Decimal("0.01")})
    with engine.begin() as connection:
        report = plan(connection)
        assert report["area_check"] == "missing" and report["invalid_areas"] == [{"id": 1, "area": "1000000000.01"}]
        with pytest.raises(ValueError, match="Применение остановлено"):
            migrate(connection, lambda: pytest.fail("Unexpected backup"))
    with engine.begin() as connection:
        assert plan(connection)["area_check"] == "missing"
        assert connection.scalar(text("SELECT area FROM properties WHERE id = 1")) == Decimal("1000000000.01")


def test_area_migration_changes_nothing_when_backup_fails(engine):
    def failed_backup():
        raise OSError("backup failed")

    with engine.begin() as connection:
        legacy_schema(connection)
        before = snapshot(connection)
    with pytest.raises(OSError), engine.begin() as connection:
        migrate(connection, failed_backup)
    with engine.begin() as connection:
        assert plan(connection)["area_check"] == "missing"
        assert snapshot(connection) == before


def test_area_migration_changes_nothing_when_the_table_is_busy(engine):
    with engine.begin() as connection:
        legacy_schema(connection)
    blocker = engine.connect()
    held = blocker.begin()
    blocker.execute(text("LOCK TABLE properties IN ACCESS EXCLUSIVE MODE"))
    try:
        with pytest.raises(DBAPIError) as error, engine.begin() as connection:
            migrate(connection, lambda: pytest.fail("Unexpected backup"))
        assert error.value.orig.sqlstate == "55P03"
    finally:
        held.rollback()
        blocker.close()
    with engine.begin() as connection:
        assert plan(connection)["area_check"] == "missing"


def test_area_migration_preserves_records_and_links(engine, client):
    agreement, _ = create_agreement(client)
    accrual = make_accrual(client, agreement["id"], amount="100")
    create(client, "payments", {"accrual_id": accrual["id"], "amount": "50", "payment_date": day()})
    with engine.begin() as connection:
        legacy_schema(connection)
        before = snapshot(connection)
        assert migrate(connection, lambda: "verified.dump")["status"] == "migrated"
        assert snapshot(connection) == before
    assert client.get(f"/api/accruals/{accrual['id']}").json()["paid"] == "50.00"
