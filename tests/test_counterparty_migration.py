import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from backend.migrate_counterparty import migrate, plan
from backend.models import Contract
from backend.seed import legacy_individual_tax_number, make_parties
from backend.services.counterparty import clean_email, valid_tax_checksum
from backend.services.finance import financial_totals
from conftest import AS_OF
from test_application import create, create_agreement, make_accrual


def legacy(connection):
    connection.execute(text("""
        ALTER TABLE counterparties
        DROP CONSTRAINT unique_counterparty_tax_id,
        DROP CONSTRAINT valid_counterparty_tax_id,
        ALTER COLUMN tax_id DROP NOT NULL
    """))
    connection.execute(text("ALTER TABLE counterparties RENAME COLUMN email TO contact"))


def test_seed_counterparty_requisites():
    parties = make_parties()
    assert len({party.tax_id for party in parties}) == len(parties)
    for party in parties:
        assert valid_tax_checksum(party.tax_id)
        assert clean_email(party.email) == party.email and party.email.endswith(".invalid")


def test_migration_preserves_contract_references_and_finance(engine, client):
    agreement, base = create_agreement(client)
    id = base["counterparty_id"]
    accrual = make_accrual(client, agreement["id"], amount="100.00")
    create(client, "payments", {"accrual_id": accrual["id"], "amount": "20.00", "payment_date": AS_OF.isoformat()})
    parties = make_parties()
    demo_index, demo = next((i, party) for i, party in enumerate(parties, 1) if party.name.startswith("ИП "))
    with engine.begin() as connection:
        legacy(connection)
        connection.execute(text("UPDATE counterparties SET tax_id = '0012-345678', contact = ' office@example.com '"))
        connection.execute(text("INSERT INTO counterparties (name, tax_id, contact) VALUES (:name, :tax, :contact)"), [
            {"name": demo.name, "tax": legacy_individual_tax_number(demo_index), "contact": demo.email},
            {"name": "Прочая организация", "tax": "009876543210", "contact": "+7 333 1234567"},
            {"name": "Без почты", "tax": "009876543211", "contact": " "},
        ])
        expected_ids = connection.scalars(text("SELECT id FROM counterparties ORDER BY id")).all()
        expected_finance = financial_totals(connection, Contract.counterparty_id == id, AS_OF)
        report = plan(connection)[0]
        assert report["corrected_demo_tax_ids"] == report["normalized_tax_ids"] == 1
        assert report["unusable_contacts"] == report["trimmed_emails"] == report["blank_contacts"] == 1
        assert report["valid_emails"] == 2
    with engine.begin() as connection:
        def backup():
            assert "contact" in {column["name"] for column in inspect(connection).get_columns("counterparties")}
            return "verified.dump"

        assert migrate(connection, backup)["version"] == "migrated"
    with engine.begin() as connection:
        assert migrate(connection, lambda: pytest.fail("Unexpected backup"))["version"] == "current"
        assert connection.scalars(text("SELECT id FROM counterparties ORDER BY id")).all() == expected_ids
        assert connection.execute(text("SELECT tax_id, email FROM counterparties ORDER BY id")).all() == [
            ("0012345678", "office@example.com"), (demo.tax_id, demo.email),
            ("009876543210", None), ("009876543211", None),
        ]
    response = client.get(f"/api/counterparties/{id}").json()
    assert response["contract_count"] == 1
    assert response["finance"] == expected_finance.model_dump(mode="json")
    assert client.get(f"/api/contracts/{agreement['id']}").json()["counterparty_id"] == id

    for bad_tax_id, state in [(None, "23502"), ("", "23514"), ("00000000000", "23514"),
                              ("abcdef1234", "23514"), ("0012345678", "23505")]:
        with pytest.raises(IntegrityError) as error, engine.begin() as connection:
            connection.execute(text("INSERT INTO counterparties (name, tax_id) VALUES ('Проверка', :tax)"), {"tax": bad_tax_id})
        assert error.value.orig.sqlstate == state


@pytest.mark.parametrize("identifiers", [
    [None], [" "], ["0000074501130"], ["000000000a"], ["1234-567890", "1234567890"],
])
def test_migration_blocks_unresolved_tax_ids(engine, identifiers):
    with engine.begin() as connection:
        legacy(connection)
        for value in identifiers:
            connection.execute(text("INSERT INTO counterparties (name, tax_id) VALUES ('Проверка', :tax)"), {"tax": value})
    with pytest.raises(ValueError, match="неразрешённые ИНН"), engine.begin() as connection:
        migrate(connection, lambda: pytest.fail("Unexpected backup"))
    with engine.connect() as connection:
        assert connection.scalars(text("SELECT tax_id FROM counterparties ORDER BY id")).all() == identifiers
        assert plan(connection)[0]["version"] == "legacy"


def test_migration_rolls_back_the_column_and_data_on_ddl_failure(engine):
    with engine.begin() as connection:
        legacy(connection)
        connection.execute(text("ALTER TABLE counterparties ADD CONSTRAINT valid_counterparty_tax_id CHECK (true)"))
        connection.execute(text("INSERT INTO counterparties (name, tax_id, contact) VALUES ('Проверка', '12-34567890', 'Офис')"))
    with pytest.raises(ProgrammingError), engine.begin() as connection:
        migrate(connection, lambda: "verified.dump")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT tax_id, contact FROM counterparties")).one() == ("12-34567890", "Офис")
