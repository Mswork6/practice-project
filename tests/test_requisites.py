import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from backend.migrate_requisites import CHECK, UNIQUE, migrate, plan
from backend.seed import legacy_individual_tax_number, make_parties
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


@pytest.mark.parametrize("value", ["1:2:3", "1:2:3:4:5", "1::3:4", "1:2:xyz:4", "１:2:3:4", "9" * 75 + ":1:2:34"])
def test_property_rejects_malformed_cadastral_number(client, value):
    response = client.post("/api/properties", json={**PROPERTY, "cadastral_number": value})
    assert response.status_code == 422
    assert "cadastral_number" in fields(response)


def test_cadastral_number_lifecycle(client):
    first = create(client, "properties", {**PROPERTY, "cadastral_number": " 00:2:00003:004 "})
    second = create(client, "properties", {**PROPERTY, "cadastral_number": " "})
    assert first["cadastral_number"] == "00:2:00003:004"
    assert second["cadastral_number"] is None
    assert create(client, "properties", PROPERTY)["cadastral_number"] is None
    first_url, second_url = (f"/api/properties/{item['id']}" for item in (first, second))
    number = {"cadastral_number": first["cadastral_number"]}
    assert client.patch(first_url, json=number).status_code == 200
    assert client.patch(first_url, json={"status": "archived"}).status_code == 200
    for response in (client.post("/api/properties", json=PROPERTY | number), client.patch(second_url, json=number)):
        assert response.status_code == 409
        assert "cadastral_number" in fields(response)
    assert client.patch(second_url, json={"cadastral_number": "1:02:5:607"}).json()["cadastral_number"] == "1:02:5:607"
    assert client.patch(second_url, json={"cadastral_number": ""}).json()["cadastral_number"] is None


def test_concurrent_cadastral_create(client):
    def submit():
        return client.post("/api/properties", json={**PROPERTY, "cadastral_number": "0:1:02:0003"})

    assert sorted(concurrent(submit, submit)) == [201, 409]


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
    record = create(client, "properties", {"name": " «A» (№ 3-A) ", "address": " квартал 11, 3-й км / уч. № 2 ",
                                           "type": "land", "area": "999999999999,99", "note": " Первая\nВторая "})
    assert record["name"] == "«A» (№ 3-A)"
    assert record["address"] == "квартал 11, 3-й км / уч. № 2"
    assert record["area"] == "999999999999.99"
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


def legacy_properties(connection):
    connection.execute(text(f"ALTER TABLE properties DROP CONSTRAINT {UNIQUE}, DROP CONSTRAINT {CHECK}"))


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
        legacy_properties(connection)
        connection.execute(text("UPDATE properties SET cadastral_number = ' 00:1:002:3 ' WHERE id = :id"),
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
        assert connection.scalars(text("SELECT cadastral_number FROM properties ORDER BY id")).all() == ["00:1:002:3", None]
        assert connection.scalar(text("SELECT tax_id FROM counterparties WHERE name = :name"), {"name": demo.name}) == demo.tax_id
        assert migrate(connection, lambda: pytest.fail("Unexpected backup"))["status"] == "current"


@pytest.mark.parametrize("conflict", ["tax", "duplicate", "malformed"])
def test_migration_blocks_conflicts_before_backup(engine, conflict):
    with engine.begin() as connection:
        legacy_properties(connection)
        if conflict == "tax":
            connection.execute(text("INSERT INTO counterparties (name, tax_id) VALUES ('A', '0012345678')"))
        elif conflict == "duplicate":
            connection.execute(INSERT_PROPERTY, [{"value": " 0:1:2:3 "}, {"value": "0:1:2:3"}])
        else:
            connection.execute(INSERT_PROPERTY, {"value": "A"})
        with pytest.raises(ValueError, match="Применение остановлено"):
            migrate(connection, lambda: pytest.fail("Unexpected backup"))
        assert not plan(connection)[0]["constraints_installed"]


def test_migration_rolls_back_when_ddl_fails(engine):
    with engine.begin() as connection:
        legacy_properties(connection)
        connection.execute(text(f"CREATE INDEX {UNIQUE} ON properties (id)"))
        connection.execute(INSERT_PROPERTY, {"value": " "})
    with pytest.raises(ProgrammingError), engine.begin() as connection:
        migrate(connection, lambda: "verified.dump")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT cadastral_number FROM properties")) == " "
        assert not {UNIQUE, CHECK} & {
            item["name"] for item in inspect(connection).get_unique_constraints("properties")
            + inspect(connection).get_check_constraints("properties")
        }


@pytest.mark.parametrize(("value", "state"), [("1:2:3", "23514"), ("1:2:3:4\n", "23514"), ("00:1:2:3", "23505")])
def test_postgresql_checks_direct_writes(engine, client, value, state):
    create(client, "properties", PROPERTY | {"cadastral_number": "00:1:2:3", "status": "archived"})
    with pytest.raises(IntegrityError) as error, engine.begin() as connection:
        connection.execute(INSERT_PROPERTY, {"value": value})
    assert error.value.orig.sqlstate == state
