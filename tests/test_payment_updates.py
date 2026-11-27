from decimal import Decimal

import pytest

from test_application import chart, create, create_agreement, make_accrual, series
from test_business_rules import concurrent, day


def prepare(client, amount="50000", **dates):
    agreement, _ = create_agreement(client)
    return make_accrual(client, agreement["id"], amount=amount, **dates)


def payment(client, accrual, amount, date=None):
    return create(client, "payments", {
        "accrual_id": accrual["id"], "amount": amount, "payment_date": date or day(),
    })


def payment_url(record):
    return f"/api/payments/{record['id']}"


def accrual_url(record):
    return f"/api/accruals/{record['id']}"


def test_payment_edit_excludes_itself_from_limit_and_updates_dashboard(client):
    accrual = prepare(client)
    first = payment(client, accrual, "20000.00")
    payment(client, accrual, "15000.00")
    url = payment_url(first)
    detail = client.get(url).json()
    assert detail["contract_number"] == "ТЕСТ-1"
    assert detail["accrual_date"] == accrual["accrual_date"]
    assert Decimal(detail["payment_limit"]) == 35000
    assert client.patch(url, json={"amount": "35000.01"}).status_code == 409
    result = client.patch(url, json={"amount": "35000.00"})
    assert result.status_code == 200
    assert result.json() == {**first, "amount": "35000.00"}
    assert client.get(accrual_url(accrual)).json()["remaining"] == "0.00"
    assert client.get("/api/accruals", params={"status": "paid"}).json()["total"] == 1
    assert Decimal(chart(client)["kpis"]["income"]) == 50000
    assert Decimal(chart(client)["kpis"]["debt"]) == 0


@pytest.mark.parametrize("patch", [
    {"amount": "0"}, {"amount": "-1"}, {"amount": "1.001"}, {"amount": None},
    {"payment_date": day(1)}, {"payment_date": "2026-09-09"}, {"payment_date": None},
    {"accrual_id": 99},
])
def test_payment_edit_validation(client, patch):
    accrual = prepare(client)
    original = payment(client, accrual, "20000.00")
    url = payment_url(original)
    assert client.patch(url, json=patch).status_code == 422
    assert client.get(url).json()["amount"] == original["amount"]
    assert client.get(url).json()["payment_date"] == original["payment_date"]


def test_payment_date_moves_month_year_and_historical_debt(client):
    accrual = prepare(client, amount="100.00", accrual_date="2025-11-25", due_date="2025-11-30")
    original = payment(client, accrual, "40.00", "2025-12-15")

    def annual(year):
        return chart(client, period="year", year=year)

    assert Decimal(annual(2025)["kpis"]["income"]) == 40
    assert series(annual(2025)["panels"]["debt"])["Дек 2025"] == 60
    result = client.patch(payment_url(original), json={"payment_date": "2026-01-15"})
    assert result.status_code == 200
    assert result.json()["payment_date"] == "2026-01-15"
    assert annual(2025)["kpis"]["income"] == "0.00"
    assert series(annual(2025)["panels"]["debt"])["Дек 2025"] == 100
    january = annual(2026)
    assert series(january["panels"]["income"])["Янв 2026"] == 40
    assert series(january["panels"]["debt"])["Янв 2026"] == 60
    assert Decimal(january["kpis"]["income"]) == 40


def test_remove_payment_restores_editing_only_after_last_payment(client):
    agreement, _ = create_agreement(client)
    accrual = make_accrual(client, agreement["id"], amount="100.00")
    client.post(f"/api/contracts/{agreement['id']}/complete")
    first, last = payment(client, accrual, "20.00"), payment(client, accrual, "50.00")
    url = accrual_url(accrual)
    assert client.delete(payment_url(first)).status_code == 204
    assert client.get(url).json()["overdue"] == "50.00"
    assert client.patch(url, json={"amount": "150.00"}).status_code == 409
    assert client.delete(url).status_code == 409
    assert client.get(payment_url(last)).status_code == 200
    assert client.patch(payment_url(last), json={"amount": "75.00"}).status_code == 200
    assert client.delete(payment_url(last)).status_code == 204
    assert client.get(url).json()["has_payments"] is False
    assert client.get(url).json()["payment_limit"] == "100.00"
    assert chart(client)["kpis"]["income"] == "0.00"
    assert Decimal(chart(client)["kpis"]["debt"]) == 100
    assert client.patch(url, json={"amount": "150.00"}).status_code == 200
    assert client.delete(url).status_code == 204


@pytest.mark.parametrize("method", ["PATCH", "DELETE", "GET"])
def test_missing_payment(client, method):
    assert client.request(method, "/api/payments/1", json={"amount": "30.00"} if method == "PATCH" else None).status_code == 404


@pytest.mark.parametrize("second", ["create", "edit"])
def test_concurrent_payment_changes_cannot_overpay(client, second):
    accrual = prepare(client, amount="100.00")
    first = payment(client, accrual, "20.00")
    another = payment(client, accrual, "20.00")
    if second == "create":
        request = lambda: client.post("/api/payments", json={"accrual_id": accrual["id"], "amount": "25.00", "payment_date": day()})
        expected = {(200, 409), (409, 201)}
    else:
        request = lambda: client.patch(payment_url(another), json={"amount": "70.00"})
        expected = {(200, 409), (409, 200)}
    result = concurrent(lambda: client.patch(payment_url(first), json={"amount": "75.00"}), request)
    assert result in expected
    assert Decimal(client.get(accrual_url(accrual)).json()["paid"]) <= 100


@pytest.mark.parametrize("second", ["edit_payment", "edit_accrual"])
def test_concurrent_delete_and_edit_are_serialized(client, second):
    accrual = prepare(client, amount="100.00")
    original = payment(client, accrual, "20.00")
    url = payment_url(original)
    if second == "edit_payment":
        request = lambda: client.patch(url, json={"amount": "40.00"})
        expected = {(204, 404), (204, 200)}
    else:
        request = lambda: client.patch(accrual_url(accrual), json={"amount": "40.00"})
        expected = {(204, 409), (204, 200)}
    result = concurrent(lambda: client.delete(url), request)
    assert result in expected
    assert client.get(url).status_code == 404
    assert client.get(accrual_url(accrual)).json()["has_payments"] is False
