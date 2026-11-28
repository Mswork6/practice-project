import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from backend.migrate_requisites import CHECK, UNIQUE, migrate, plan
from backend.seed import legacy_individual_tax_number, make_parties
from backend.services.requisites import CADASTRAL_KEY_INDEX
from conftest import AS_OF
from test_application import PROPERTY, create, create_agreement, make_accrual
from test_business_rules import concurrent

INSERT_PROPERTY = text("""
    INSERT INTO properties (name, type, address, cadastral_number, status, created_at)
    VALUES ('A', 'land', 'B', :value, 'available', CURRENT_DATE)
""")


def fields(response):
    payload = response.json()
    return {error["loc"][-1]: error["msg"] for error in payload.get("errors", payload["detail"])}


@pytest.mark.parametrize("value", [
    "1:2:3:4", "744:36:0703001:125", "74:366:0703001:125", "74:36:123:125",
    "74:36:00000001:125", "74:36:0703001:0125", "74:36:0703001:0", "74:36:0703001:000",
    "74:36:ABC:125", "74:36:0703001", "74:36:0703001:125:1", "74::0703001:125",
    "７４:36:0703001:125", "74:36:0703001:" + "9" * 67, "0:0:0:0125",
    "00:00:0000000:125", "74:0:000001:125",
])
def test_property_rejects_malformed_cadastral_number(client, value):
    response = client.post("/api/properties", json={**PROPERTY, "cadastral_number": value})
    assert response.status_code == 422
    assert "cadastral_number" in fields(response)


@pytest.mark.parametrize("value", [
    "74:36:0703001:125", "74:36:070301:1", "02:05:0102003:12589", "74:36:0703001:123456",
    "0:0:0:125", "74:36:0:125", "74:0:0:125", "74:00:0000000:125",
    "74:36:0703001:" + "9" * 66,
])
def test_property_preserves_valid_cadastral_spelling(client, value):
    assert create(client, "properties", PROPERTY | {"cadastral_number": f" {value} "})["cadastral_number"] == value


def test_cadastral_number_lifecycle(client):
    first = create(client, "properties", {**PROPERTY, "cadastral_number": " 02:05:0102003:125 "})
    second = create(client, "properties", {**PROPERTY, "cadastral_number": " "})
    assert first["cadastral_number"] == "02:05:0102003:125"
    assert second["cadastral_number"] is None
    assert create(client, "properties", PROPERTY)["cadastral_number"] is None
    first_url, second_url = (f"/api/properties/{item['id']}" for item in (first, second))
    number = {"cadastral_number": first["cadastral_number"]}
    assert client.patch(first_url, json=number).status_code == 200
    assert client.patch(first_url, json={"cadastral_number": "02:05:102003:125"}).status_code == 200
    assert client.patch(first_url, json=number).status_code == 200
    assert client.patch(first_url, json={"status": "archived"}).status_code == 200
    for response in (
        client.post("/api/properties", json=PROPERTY | number),
        client.patch(second_url, json={"cadastral_number": "02:05:102003:125"}),
    ):
        assert response.status_code == 409
        assert "cadastral_number" in fields(response)
    assert client.patch(second_url, json={"cadastral_number": "74:36:070301:1"}).json()["cadastral_number"] == "74:36:070301:1"
    assert client.patch(second_url, json={"cadastral_number": ""}).json()["cadastral_number"] is None


@pytest.mark.parametrize("second", ["02:05:0102003:125", "02:05:102003:125"])
def test_concurrent_cadastral_create(client, second):
    def submit(number):
        return client.post("/api/properties", json={**PROPERTY, "cadastral_number": number})

    assert sorted(concurrent(lambda: submit("02:05:0102003:125"), lambda: submit(second))) == [201, 409]


@pytest.mark.parametrize("value", ["0", "-1", "NaN", "Infinity", "1.001", "1.230", "1000000000000", True])
def test_area_rejects_invalid_numbers(client, value):
    response = client.post("/api/properties", json={**PROPERTY, "area": value})
    assert response.status_code == 422 and "area" in fields(response)


@pytest.mark.parametrize(("field", "value"), [("name", "   "), ("address", "   "), ("name", "Я" * 201),
                                              ("address", "Я" * 301), ("note", "Я" * 3001), ("type", "invalid")])
def test_property_form_field_errors(client, field, value):
    response = client.post("/api/properties", json=PROPERTY | {field: value})
    assert response.status_code == 422 and field in fields(response)


def test_property_accepts_text_punctuation_multiline_and_comma_decimal(client):
    # The area is capped at 1 000 000 000 m², while money keeps the full NUMERIC(14, 2) range.
    record = create(client, "properties", {"name": " «A» (№ 3-A) ", "address": " квартал 11, 3-й км / уч. № 2 ",
                                           "type": "land", "area": "999999999,99", "note": " Первая\nВторая "})
    assert record["name"] == "«A» (№ 3-A)"
    assert record["address"] == "квартал 11, 3-й км / уч. № 2"
    assert record["area"] == "999999999.99"
    assert record["note"] == "Первая\nВторая"


def test_contract_and_finance_field_errors(client):
    agreement, base = create_agreement(client)
    for patch, field in [
        ({"end_date": "2025-01-01"}, "end_date"), ({"end_date": None}, "end_date"),
        ({"start_date": "2026-02-30"}, "start_date"), ({"property_id": 2100000000}, "property_id"),
        ({"counterparty_id": 2100000000}, "counterparty_id"), ({}, "number"),
    ]:
        response = client.post("/api/contracts", json=base | patch)
        assert response.status_code in (404, 409, 422) and field in fields(response)

    accrual = make_accrual(client, agreement["id"], amount="100.00")
    response = client.patch(f"/api/accruals/{accrual['id']}", json={"due_date": "2020-01-01"})
    assert response.status_code == 422 and "due_date" in fields(response)
    response = client.post("/api/payments", json={
        "accrual_id": accrual["id"], "amount": "100.01", "payment_date": AS_OF.isoformat(),
    })
    assert response.status_code == 409 and "amount" in fields(response)


def legacy_properties(connection, *, without_check=False):
    connection.execute(text(f"DROP INDEX {CADASTRAL_KEY_INDEX}"))
    connection.execute(text(f"ALTER TABLE properties DROP CONSTRAINT {CHECK}"))
    if not without_check:
        connection.execute(text(
            f"ALTER TABLE properties ADD CONSTRAINT {CHECK} CHECK (cadastral_number ~ '^[0-9]+:[0-9]+:[0-9]+:[0-9]+$')"
        ))


def test_migration_replaces_an_existing_check_without_replacing_unique(engine):
    with engine.begin() as connection:
        legacy_properties(connection)
        oid = text("SELECT oid FROM pg_constraint WHERE conrelid = 'properties'::regclass AND conname = :name")
        unique_before = connection.scalar(oid, {"name": UNIQUE})
        check_before = connection.scalar(oid, {"name": CHECK})
        connection.execute(INSERT_PROPERTY, {"value": "74:36:0703001:125"})
        assert not plan(connection)[0]["constraints_installed"]
        assert migrate(connection, lambda: "verified.dump")["status"] == "migrated"
        assert connection.scalar(oid, {"name": UNIQUE}) == unique_before
        assert connection.scalar(oid, {"name": CHECK}) != check_before
        assert next(index for index in inspect(connection).get_indexes("properties")
                    if index["name"] == CADASTRAL_KEY_INDEX)["unique"]
        assert migrate(connection, lambda: pytest.fail("Unexpected backup"))["status"] == "current"


def test_migration_preserves_links_and_finance_and_is_idempotent(engine, client):
    agreement, base = create_agreement(client)
    accrual = make_accrual(client, agreement["id"], amount="100.00")
    create(client, "payments", {"accrual_id": accrual["id"], "amount": "50.00", "payment_date": AS_OF.isoformat()})
    create(client, "properties", PROPERTY)
    demo_index, demo = next((i, p) for i, p in enumerate(make_parties(), 1) if p.name.startswith("ИП "))

    def snapshot(connection):
        return [connection.scalars(text(
            f"SELECT to_jsonb(t){exclude} FROM {table} t ORDER BY id"
        )).all() for table, exclude in (
            ("properties", " - 'cadastral_number'"), ("counterparties", " - 'tax_id'"),
            ("contracts", ""), ("accruals", ""), ("payments", ""),
        )]

    with engine.begin() as connection:
        legacy_properties(connection, without_check=True)
        connection.execute(text("UPDATE properties SET cadastral_number = ' 02:05:0102003:125 ' WHERE id = :id"),
                           {"id": base["property_id"]})
        connection.execute(text("UPDATE properties SET cadastral_number = ' ' WHERE cadastral_number IS NULL"))
        # Simulate an older seed row placed in a schema before the length check existed.
        connection.execute(text("ALTER TABLE counterparties DROP CONSTRAINT valid_counterparty_tax_id"))
        connection.execute(text("INSERT INTO counterparties (name, tax_id, email) VALUES (:name, :tax, :email)"),
                           {"name": demo.name, "tax": legacy_individual_tax_number(demo_index), "email": demo.email})
        before = snapshot(connection)
        assert plan(connection)[0]["tax_id"]["confirmed_demo_corrections"] == 1
        assert migrate(connection, lambda: "verified.dump")["status"] == "migrated"
        assert snapshot(connection) == before
        assert connection.scalars(text("SELECT cadastral_number FROM properties ORDER BY id")).all() == ["02:05:0102003:125", None]
        assert connection.scalar(text("SELECT tax_id FROM counterparties WHERE name = :name"), {"name": demo.name}) == demo.tax_id
        assert migrate(connection, lambda: pytest.fail("Unexpected backup"))["status"] == "current"


@pytest.mark.parametrize("conflict", ["tax", "duplicate", "malformed"])
def test_migration_blocks_conflicts_before_backup(engine, conflict):
    with engine.begin() as connection:
        legacy_properties(connection)
        if conflict == "tax":
            connection.execute(text("INSERT INTO counterparties (name, tax_id) VALUES ('A', '0012345678')"))
        elif conflict == "duplicate":
            connection.execute(INSERT_PROPERTY, [{"value": "74:0:0:125"}, {"value": "74:00:0000000:125"}])
            assert plan(connection)[0]["cadastral_number"]["duplicate_groups"] == 1
        else:
            connection.execute(INSERT_PROPERTY, {"value": "1:2:3:4"})
        with pytest.raises(ValueError, match="Применение остановлено"):
            migrate(connection, lambda: pytest.fail("Unexpected backup"))
        assert not plan(connection)[0]["constraints_installed"]


def test_migration_rolls_back_when_ddl_fails(engine):
    with engine.begin() as connection:
        legacy_properties(connection)
        connection.execute(text(f"CREATE INDEX {CADASTRAL_KEY_INDEX} ON properties (id)"))
    with pytest.raises(ProgrammingError), engine.begin() as connection:
        migrate(connection, lambda: "verified.dump")
    with engine.begin() as connection:
        connection.execute(INSERT_PROPERTY, {"value": "1:2:3:4"})
        assert not plan(connection)[0]["constraints_installed"]


@pytest.mark.parametrize(("value", "state"), [
    ("1:2:3:4", "23514"), ("74:36:0703001:125\n", "23514"), ("74:36:0703001:0125", "23514"),
    ("74:36:0703001:125", "23505"), ("74:36:703001:125", "23505"),
])
def test_postgresql_checks_direct_writes(engine, client, value, state):
    create(client, "properties", PROPERTY | {"cadastral_number": "74:36:0703001:125", "status": "archived"})
    with pytest.raises(IntegrityError) as error, engine.begin() as connection:
        connection.execute(INSERT_PROPERTY, {"value": value})
    assert error.value.orig.sqlstate == state
