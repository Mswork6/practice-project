from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.main import app
from backend.models import Accrual, Contract, Payment, Property
from backend.seed import populate
from conftest import AS_OF

PROPERTY = {"name": "Тестовое здание", "type": "building", "address": "г. Береговой, ул. Новая, 15"}
CONTRACT = {
    "number": "ТЕСТ-1", "type": "real_estate_rent", "start_date": "2026-01-01",
    "end_date": (AS_OF + timedelta(days=20)).isoformat(), "amount": "75000.50",
}


def create(client, endpoint, data):
    response = client.post(f"/api/{endpoint}", json=data)
    assert response.status_code == 201, response.json()
    return response.json()


def create_agreement(client, **changes):
    prop = create(client, "properties", PROPERTY)
    party = create(client, "counterparties", {"name": "Тестовый контрагент", "tax_id": "660076500000"})
    body = {**CONTRACT, "property_id": prop["id"], "counterparty_id": party["id"], **changes}
    return create(client, "contracts", body), body


def make_accrual(client, contract_id, **changes):
    return create(client, "accruals", {
        "contract_id": contract_id, "amount": "25000.50", "accrual_date": "2026-09-10",
        "due_date": "2026-09-20", **changes,
    })


def chart(client, **params):
    response = client.get("/api/dashboard", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def series(panel):
    return {point["label"]: Decimal(str(point["value"])) for point in panel["trend"]}


def test_empty_application(client):
    assert client.get("/").status_code == 200
    assert "Дашборд" in client.get("/").text
    assert client.get("/static/vendor/chart.umd.min.js").status_code == 200
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/api/properties").json() == {"items": [], "total": 0, "page": 1, "page_size": 20}
    assert client.get("/api/properties/1").status_code == 404
    data = chart(client, period="all")
    assert data["period_start"] is None
    assert data["kpis"] == {"income": "0.00", "debt": "0.00", "properties": 0, "renewals": 0}
    assert list(data["panels"]) == ["income", "debt", "properties", "renewals"]
    assert len(data["panels"]["debt"]["structure"]) == 4
    assert len(data["panels"]["properties"]["structure"]) == 4
    assert len(data["panels"]["renewals"]["structure"]) == 4


def test_full_workflow_and_persistence(client, engine):
    agreement, inputs = create_agreement(client)
    assert chart(client)["kpis"] == {"income": "0.00", "debt": "0.00", "properties": 1, "renewals": 1}
    assert agreement["days_remaining"] == 20

    accrual = make_accrual(client, agreement["id"])
    assert Decimal(chart(client)["kpis"]["debt"]) == Decimal("25000.50")
    payment = create(client, "payments", {
        "accrual_id": accrual["id"], "amount": "5100.25", "payment_date": AS_OF.isoformat(),
    })
    data = chart(client)
    assert Decimal(data["kpis"]["income"]) == Decimal("5100.25")
    assert Decimal(data["kpis"]["debt"]) == Decimal("19900.25")
    detail = client.get(f"/api/accruals/{accrual['id']}").json()
    assert [Decimal(detail[key]) for key in ("paid", "remaining", "overdue")] == [
        Decimal("5100.25"), Decimal("19900.25"), Decimal("19900.25"),
    ]
    history = client.get("/api/payments", params={"accrual_id": accrual["id"]}).json()
    assert history["items"] == [payment]

    property_url = f"/api/properties/{inputs['property_id']}"
    assert client.delete(property_url).status_code == 409
    assert client.delete(f"/api/contracts/{agreement['id']}").status_code == 409
    assert client.patch(property_url, json={"status": "archived"}).status_code == 409
    assert client.patch(f"/api/contracts/{agreement['id']}", json={"status": "completed"}).status_code == 200
    assert client.patch(property_url, json={"status": "archived"}).status_code == 200
    assert chart(client)["kpis"]["properties"] == 0

    # Close pooled DB connections and use a fresh application client.
    engine.dispose()
    with TestClient(app) as restarted:
        assert restarted.get(property_url).json()["status"] == "archived"
        assert restarted.get(f"/api/payments/{payment['id']}").json() == payment
        assert chart(restarted)["kpis"]["income"] == data["kpis"]["income"]


def test_historical_cutoffs_and_current_kpis_ignore_period(client, engine):
    contract, _ = create_agreement(client)
    accrual = make_accrual(client, contract["id"], amount="25000", due_date="2026-09-30")
    for amount, day in (("20000", "2026-09-28"), ("1000", "2026-11-15")):
        create(client, "payments", {"accrual_id": accrual["id"], "amount": amount, "payment_date": day})
    # Simulate a legacy payment accepted before the new date validation.
    with Session(engine) as db, db.begin():
        db.add(Payment(accrual_id=accrual["id"], amount=Decimal("4000"), payment_date=date(2026, 12, 1)))
    current = chart(client)
    debt = series(current["panels"]["debt"])
    assert debt["Сен 2026"] == 0  # due date is inclusive
    assert debt["Окт 2026"] == 5000  # November and December payments are excluded
    assert debt["Ноя 2026"] == 4000
    assert Decimal(current["kpis"]["income"]) == 21000
    assert Decimal(current["kpis"]["debt"]) == 4000
    balance = client.get(f"/api/accruals/{accrual['id']}").json()
    assert [Decimal(balance[key]) for key in ("paid", "remaining", "overdue", "payment_limit")] == [
        Decimal("21000"), Decimal("4000"), Decimal("4000"), Decimal("0"),
    ]
    assert balance["has_payments"]

    prior = chart(client, period="year", year=2025)
    assert Decimal(prior["kpis"]["income"]) == 0
    assert prior["kpis"]["debt"] == current["kpis"]["debt"]
    assert prior["kpis"]["properties"] == current["kpis"]["properties"]
    assert prior["kpis"]["renewals"] == current["kpis"]["renewals"]
    assert prior["panels"]["debt"]["structure"] == current["panels"]["debt"]["structure"]
    assert all(value == 0 for value in series(prior["panels"]["debt"]).values())
    assert chart(client, period="all")["period_start"] == "2026-09-10"
    assert client.get("/api/dashboard", params={"period": "year", "year": 2027}).status_code == 422


def test_term_windows_and_category_labels(client):
    _, base = create_agreement(client)
    for days in (-1, 0, 30, 31, 90, 91, 180, 181):
        result = create(client, "contracts", {
            **base, "number": f"ГРАНИЦА-{days}", "end_date": (AS_OF + timedelta(days=days)).isoformat(),
        })
        assert result["days_remaining"] == days
    create(client, "contracts", {**base, "number": "ЗАВЕРШЁН", "status": "completed"})
    create(client, "contracts", {**base, "number": "РАСТОРГНУТ", "status": "terminated"})
    create(client, "contracts", {**base, "number": "ЕЩЁ НЕ НАЧАЛСЯ", "start_date": "2026-11-24"})
    create(client, "contracts", {**base, "number": "ПРОДАЖА", "type": "sale", "end_date": None})

    data = chart(client)
    assert data["kpis"]["renewals"] == 7
    assert sum(series(data["panels"]["renewals"]).values()) == 8
    assert [(item["label"], item["value"]) for item in data["panels"]["renewals"]["structure"]] == [
        ("0–30 дней", 3), ("31–90 дней", 2), ("91–180 дней", 2), ("181 день и более", 1),
    ]
    assert [item["label"] for item in data["panels"]["debt"]["structure"]] == [
        "Аренда недвижимости", "Аренда земли", "Продажа имущества", "Прочие",
    ]
    assert [item["label"] for item in data["panels"]["properties"]["structure"]] == [
        "Здания", "Земельные участки", "Нежилые помещения", "Прочие",
    ]


def test_registry_search_filters_patch_delete(client):
    agreement, body = create_agreement(client)
    prop = create(client, "properties", {
        "name": "Участок _100% тест", "type": "land", "address": "Лесная 14", "cadastral_number": "66:00:42",
    })
    for search in ("ЛЕСНАЯ", "66:00:42", "_100%"):
        assert client.get("/api/properties", params={"search": search}).json()["items"] == [prop]
    filters = {"type": "land", "status": "in_use"}
    assert client.get("/api/properties", params=filters).json()["total"] == 0
    prop_url = f"/api/properties/{prop['id']}"
    assert client.patch(prop_url, json={"status": "in_use"}).status_code == 422
    patch = client.patch(prop_url, json={"address": "Лесная 15", "area": "16.25"})
    assert patch.status_code == 200
    assert patch.json()["name"] == prop["name"]
    assert client.get("/api/properties", params={**filters, "status": "available"}).json()["total"] == 1
    assert client.delete(prop_url).status_code == 204

    assert client.get("/api/contracts", params={"search": "контрагент", "status": "active"}).json()["total"] == 1
    assert client.get("/api/contracts", params={"property_id": body["property_id"]}).json()["total"] == 1
    contract_url = f"/api/contracts/{agreement['id']}"
    assert client.patch(contract_url, json={"end_date": None}).status_code == 422
    assert client.patch(contract_url, json={"status": "terminated", "number": "НОВЫЙ"}).json()["number"] == "НОВЫЙ"
    assert client.delete(contract_url).status_code == 204
    assert client.delete(f"/api/properties/{body['property_id']}").status_code == 204


def test_validation_conflicts_and_strict_money(client):
    contract, body = create_agreement(client)
    assert client.post("/api/contracts", json=body).status_code == 409
    assert client.post("/api/contracts", json={**body, "property_id": 9999}).status_code == 404
    assert client.post("/api/contracts", json={**body, "end_date": "2025-12-31"}).status_code == 422
    assert client.post("/api/properties", json={**PROPERTY, "name": "   "}).status_code == 422
    assert client.post("/api/properties", json={**PROPERTY, "area": "0"}).status_code == 422
    assert client.post("/api/accruals", json={
        "contract_id": contract["id"], "amount": "10", "accrual_date": "2026-10-02", "due_date": "2026-10-01",
    }).status_code == 422
    accrual = make_accrual(client, contract["id"], amount="100.00")
    payment = {"accrual_id": accrual["id"], "amount": "25.90", "payment_date": "2026-11-01"}
    for change, code in (({"amount": "0"}, 422), ({"amount": "0.001"}, 422), ({"amount": "100.01"}, 409),
                         ({"payment_date": "2026-09-09"}, 422), ({"accrual_id": 9999}, 404)):
        assert client.post("/api/payments", json={**payment, **change}).status_code == code
    assert client.get("/api/payments").json()["total"] == 0


def test_concurrent_payments_lock_same_accrual(client):
    agreement, _ = create_agreement(client)
    accrual = make_accrual(client, agreement["id"], amount="100.00")
    gate = Barrier(2)

    def post_payment():
        gate.wait(timeout=5)
        return client.post("/api/payments", json={
            "accrual_id": accrual["id"], "amount": "70.00", "payment_date": AS_OF.isoformat(),
        }).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = (pool.submit(post_payment) for _ in range(2))
        assert sorted([first.result(timeout=10), second.result(timeout=10)]) == [201, 409]

    result = client.get(f"/api/accruals/{accrual['id']}").json()
    assert Decimal(result["remaining"]) == Decimal("30.00")
    assert client.get("/api/payments").json()["total"] == 1


@pytest.mark.parametrize("status, expected", [("overdue", 1), ("open", 2), ("paid", 1)])
def test_accrual_filter(client, status, expected):
    agreement, _ = create_agreement(client)
    make_accrual(client, agreement["id"])
    make_accrual(client, agreement["id"], due_date="2026-12-01")
    paid = make_accrual(client, agreement["id"])
    create(client, "payments", {"accrual_id": paid["id"], "amount": "25000.50", "payment_date": AS_OF.isoformat()})
    result = client.get("/api/accruals", params={"status": status}).json()
    assert result["total"] == expected


def test_seed_is_reproducible_and_idempotent(engine):
    with Session(engine) as db, db.begin():
        assert populate(db, AS_OF) == (100, 24, 124, 310, 309)
    with Session(engine) as db, db.begin():
        assert populate(db, AS_OF) is None
        counts = [db.scalar(select(func.count()).select_from(model)) for model in (Property, Contract, Accrual, Payment)]
        assert counts == [100, 124, 310, 309]
