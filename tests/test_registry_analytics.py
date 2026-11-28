from decimal import Decimal
import pytest

from test_application import chart, create, create_agreement, make_accrual
from test_business_rules import concurrent, day


def test_income_year_only_changes_income(client):
    agreement, _ = create_agreement(client)
    accrued = make_accrual(client, agreement["id"], amount="100.00", accrual_date="2025-12-01", due_date="2025-12-31")
    for date, amount in (("2025-12-15", "30.00"), ("2026-01-01", "40.00")):
        create(client, "payments", {"accrual_id": accrued["id"], "amount": amount, "payment_date": date})
    current = chart(client)
    previous = chart(client, year=2025)
    assert current["income_years"] == [2026, 2025]
    assert Decimal(current["kpis"]["income"]) == 40 and Decimal(previous["kpis"]["income"]) == 30
    assert current["period_start"] == "2026-01-01"
    assert current["panels"]["income"]["trend"][-1]["future"] is True
    assert not any(point["future"] for point in previous["panels"]["income"]["trend"])
    for kind in ("debt", "properties", "renewals"):
        assert current["panels"][kind] == previous["panels"][kind]
        assert current["kpis"][kind] == previous["kpis"][kind]
    for data in (current, previous):
        panel = data["panels"]["income"]
        assert sum(Decimal(point["value"]) for point in panel["trend"]) == Decimal(data["kpis"]["income"])
        assert sum(Decimal(point["value"]) for point in panel["structure"]) == Decimal(data["kpis"]["income"])
    debt = current["panels"]["debt"]
    assert len(debt["trend"]) == 12
    assert Decimal(debt["trend"][-1]["value"]) == sum(Decimal(point["value"]) for point in debt["structure"]) == 30


def test_contract_filters_match_analytics(client):
    first, base = create_agreement(client, end_date=day(31), start_date=day(-40))
    for offset in (0, 30, 31, 90, 91, 180, 181):
        create(client, "contracts", {**base, "number": f"Номер-{offset}", "end_date": day(offset)})
    create(client, "contracts", {**base, "number": "План", "start_date": day(1), "end_date": day(40)})
    assert client.get("/api/contracts", params={"term": "0-30", "state": "pending"}).json()["total"] == 0
    panels = chart(client)["panels"]["renewals"]
    for point in panels["structure"]:
        params = {"term": point["key"], "sort": "end_asc", "page_size": 2, "search": "Тестовое здание"}
        page = client.get("/api/contracts", params=params).json()
        assert page["total"] == point["value"]
        endings = [row["end_date"] for row in page["items"]]
        assert endings == sorted(endings)
    for point in panels["trend"]:
        assert client.get("/api/contracts", params={"expiry_month": point["key"]}).json()["total"] == point["value"]
    assert client.get("/api/contracts", params={"expiry_month": "2026-13"}).status_code == 422
    for field in ("start", "end"):
        for direction in ("asc", "desc"):
            result = client.get("/api/contracts", params={"sort": f"{field}_{direction}"}).json()["items"]
            actual = [row[f"{field}_date"] for row in result]
            assert actual == sorted(actual, reverse=direction == "desc")
    assert client.get(f"/api/contracts/{first['id']}").status_code == 200


def test_counterparty_lifecycle(client):
    party = create(client, "counterparties", {
        "name": "Городская мастерская", "tax_id": "0012 34-5673", "email": " office@example.com ",
    })
    url = f"/api/counterparties/{party['id']}"
    assert party["tax_id"] == "0012345673"
    assert party["email"] == "office@example.com"
    assert client.get("/api/counterparties", params={"search": "0012-34", "page_size": 1}).json()["total"] == 1
    conflict = client.post("/api/counterparties", json={"name": "Другая", "tax_id": "0012-345673"})
    assert conflict.status_code == 409 and "ИНН" in conflict.json()["detail"]
    assert conflict.json()["errors"][0]["loc"] == ["body", "tax_id"]
    assert client.patch(url, json={"tax_id": ""}).status_code == 422
    assert client.patch(url, json={"tax_id": None}).status_code == 422
    assert client.patch(url, json={"email": "   "}).json()["email"] is None
    assert client.get(url).json()["tax_id"] == party["tax_id"]
    assert client.delete(url).status_code == 204
    assert client.get(url).status_code == 404


def test_parallel_counterparties_cannot_duplicate_tax_id(client):
    result = concurrent(
        lambda: client.post("/api/counterparties", json={"name": "Один", "tax_id": "1234 567894"}),
        lambda: client.post("/api/counterparties", json={"name": "Два", "tax_id": "1234567894"}),
    )
    assert sorted(result) == [201, 409]


def test_counterparty_without_email_accepts_twelve_digit_identifier(client):
    party = create(client, "counterparties", {"name": "ИП", "tax_id": "000007450104"})
    assert party["tax_id"] == "000007450104" and party["email"] is None
    assert client.patch(f"/api/counterparties/{party['id']}", json={"email": None}).status_code == 200
    assert client.patch(f"/api/counterparties/{party['id']}", json={"tax_id": "0012345673"}).json()["tax_id"] == "0012345673"


@pytest.mark.parametrize("fields", [
    {}, {"tax_id": None}, {"tax_id": "  - "}, {"tax_id": "12345678901"},
    {"tax_id": "12345678ab"}, {"tax_id": "１２３４５６７８９０"}, {"tax_id": 1234567890},
    {"tax_id": "0012345678"}, {"tax_id": "000007450114"}, {"tax_id": "000007450105"},
    {"tax_id": "0000000000"}, {"tax_id": "000000000000"}, {"tax_id": "0012/345673"},
])
def test_counterparty_rejects_missing_or_invalid_tax_id(client, fields):
    assert client.post("/api/counterparties", json={"name": "Контрагент", **fields}).status_code == 422


@pytest.mark.parametrize("email", ["+7 333 1234567", "Контактное лицо", "user@example", "a..b@example.com", "a@-example.com"])
def test_counterparty_rejects_non_email_values(client, email):
    fields = {"name": "Контрагент", "tax_id": "0012345673"}
    assert client.post("/api/counterparties", json={**fields, "email": email}).status_code == 422
    party = create(client, "counterparties", fields)
    assert client.patch(f"/api/counterparties/{party['id']}", json={"email": email}).status_code == 422


def test_shared_financial_totals_follow_payment_and_accrual_updates(client):
    first, base = create_agreement(client)
    second = create(client, "contracts", {**base, "number": "Связанный"})
    party_path = f"/api/counterparties/{base['counterparty_id']}"
    first_path = f"/api/contracts/{first['id']}"
    assert client.delete(party_path).status_code == 409
    assert client.patch(party_path, json={"name": "Переименован"}).status_code == 200
    assert client.get(first_path).json()["finance"] == dict.fromkeys(("billed", "paid", "remaining", "overdue"), "0.00")
    assert client.get("/api/contracts", params={"counterparty_id": base["counterparty_id"]}).json()["total"] == 2

    due = make_accrual(client, first["id"], amount="100.00")
    planned = make_accrual(client, second["id"], amount="50.00", due_date=day(10))
    payout = create(client, "payments", {"accrual_id": due["id"], "amount": "20.00", "payment_date": day()})
    create(client, "payments", {"accrual_id": due["id"], "amount": "30.00", "payment_date": day()})
    assert client.post(f"{first_path}/complete").status_code == 200
    detail = client.get(party_path).json()
    assert (detail["contract_count"], detail["active_contract_count"]) == (2, 1)
    assert detail["finance"] == {"billed":"150.00","paid":"50.00","remaining":"100.00","overdue":"50.00"}
    assert client.patch(f"/api/payments/{payout['id']}", json={"amount": "35.00"}).status_code == 200
    assert client.get(first_path).json()["finance"]["overdue"] == "35.00"
    assert client.delete(f"/api/payments/{payout['id']}").status_code == 204
    assert client.patch(f"/api/accruals/{planned['id']}", json={"amount":"75.00"}).status_code == 200
    assert client.get(party_path).json()["finance"]["remaining"] == "145.00"
    assert client.delete(f"/api/accruals/{planned['id']}").status_code == 204
    assert client.get(party_path).json()["finance"]["overdue"] == chart(client)["kpis"]["debt"]
