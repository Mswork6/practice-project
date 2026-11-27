from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import Accrual, Contract, Payment, Property
from backend.routers import registry
from backend.seed import populate
from conftest import AS_OF
from test_application import CONTRACT, PROPERTY, chart, create, create_agreement, make_accrual


def day(offset=0):
    return (AS_OF + timedelta(days=offset)).isoformat()


def test_property_status_tracks_contract_lifecycle(client, monkeypatch):
    obj = create(client, "properties", PROPERTY)
    other = create(client, "properties", {**PROPERTY, "name": "Второй объект"})
    contact = create(client, "counterparties", {"name": "Контрагент"})
    base = {**CONTRACT, "property_id": obj["id"], "counterparty_id": contact["id"]}
    url = f"/api/properties/{obj['id']}"
    status = lambda path: client.get(path).json()["status"]
    assert status(url) == "available"
    sale = create(client, "contracts", {**base, "type": "sale", "end_date": None, "number": "ПРОДАЖА"})
    assert sale["state"] == "active"
    assert status(url) == "available"
    upcoming = create(client, "contracts", {**base, "start_date": day(1), "end_date": day(2)})
    assert upcoming["state"] == "pending"
    assert status(url) == "available"
    monkeypatch.setattr(registry, "local_today", lambda: AS_OF + timedelta(days=1))
    assert status(url) == "in_use"
    assert client.get("/api/properties", params={"status": "in_use"}).json()["total"] == 1
    monkeypatch.setattr(registry, "local_today", lambda: AS_OF + timedelta(days=3))
    assert status(url) == "available"

    primary = create(client, "contracts", {**base, "number": "ДЕЙСТВУЮЩИЙ"})
    alternate = create(client, "contracts", {**base, "number": "ВТОРОЙ"})
    assert status(url) == "in_use"
    assert client.post(f"/api/contracts/{primary['id']}/complete").status_code == 200
    assert status(url) == "in_use"
    alternate_url = f"/api/contracts/{alternate['id']}"
    second_url = f"/api/properties/{other['id']}"
    assert client.patch(alternate_url, json={"property_id": other["id"]}).status_code == 200
    assert [status(url), status(second_url)] == ["available", "in_use"]
    assert client.post(f"{alternate_url}/terminate").status_code == 200
    assert status(second_url) == "available"
    replacement = create(client, "contracts", {**base, "number": "ПРОЧИЙ", "property_id": other["id"], "type": "other"})
    alternate_url = f"/api/contracts/{replacement['id']}"
    assert status(second_url) == "available"
    assert client.patch(alternate_url, json={"type": "real_estate_rent"}).status_code == 200
    assert status(second_url) == "in_use"
    assert client.delete(alternate_url).status_code == 204
    assert status(second_url) == "available"


@pytest.mark.parametrize("status,start,end,kind,expected", [
    ("active", 1, 10, "real_estate_rent", "pending"),
    ("active", 0, 10, "real_estate_rent", "active"),
    ("active", -1, 0, "real_estate_rent", "active"),
    ("active", -5, -1, "real_estate_rent", "expired"),
    ("completed", 1, 10, "real_estate_rent", "completed"),
    ("terminated", -3, 12, "real_estate_rent", "terminated"),
    ("active", -3, None, "sale", "active"),
])
def test_states_and_filters(client, engine, status, start, end, kind, expected):
    contract, _ = create_agreement(client, start_date=day(start),
                                   end_date=day(end) if end is not None else None, type=kind)
    if status != "active":
        with Session(engine) as db, db.begin():
            db.get(Contract, contract["id"]).status = status
    assert client.get(f"/api/contracts/{contract['id']}").json()["state"] == expected
    item = client.get("/api/contracts", params={"state": expected, "page_size": 1}).json()
    assert item["total"] == 1 and item["items"][0]["state"] == expected
    assert client.get("/api/contracts", params={"state": expected, "page": 2}).json()["items"] == []


def test_archive_restrictions_and_restore(client):
    pending, base = create_agreement(client, start_date=day(1))
    url = f"/api/properties/{base['property_id']}"
    related = f"/api/contracts/{pending['id']}"
    archived = {"status": "archived"}
    assert not client.get(url).json()["can_archive"]
    assert client.patch(url, json=archived).status_code == 409
    assert client.patch(related, json={"type": "sale", "start_date": day(), "end_date": None}).status_code == 200
    assert client.get(url).json()["status"] == "available"  # A sale blocks archiving without using the property.
    assert client.patch(url, json=archived).status_code == 409
    assert client.post(f"{related}/complete").status_code == 200
    assert client.patch(url, json=archived).json()["status"] == "archived"
    assert chart(client)["kpis"]["properties"] == 0
    assert client.post("/api/contracts", json={**base, "type": "sale"}).status_code == 409
    other, _ = create_agreement(client, number="ЧУЖОЙ")
    assert client.patch(f"/api/contracts/{other['id']}", json={"property_id": base["property_id"]}).status_code == 409
    assert client.patch(related, json={"status": "active"}).status_code == 422
    assert client.patch(related, json={"start_date": day(1)}).status_code == 409
    assert client.get(related).json()["property_archived"]
    replacement = {**base, "number": "ВОЗВРАТ", "start_date": day(), "end_date": day(10)}
    assert client.post(f"{related}/successor", json=replacement).status_code == 409
    assert client.get("/api/properties", params={"exclude_archived": True}).json()["total"] == 1
    assert client.patch(url, json={"status": "available"}).json()["status"] == "available"
    assert chart(client)["kpis"]["properties"] == 2
    assert client.post(f"{related}/successor", json=replacement).status_code == 201
    assert client.get(url).json()["status"] == "in_use"


def test_rental_categories(client):
    _, base = create_agreement(client)
    objects = {kind: create(client, "properties", {**PROPERTY, "type": kind})["id"]
               for kind in ("building", "land", "premises", "other")}
    for kind, allowed in (("land_rent", {"land"}), ("real_estate_rent", {"building", "premises"}),
                          ("sale", set(objects)), ("other", set(objects))):
        valid = client.get("/api/properties", params={"contract_type": kind}).json()["items"]
        assert {item["type"] for item in valid} == allowed
        for category, id in objects.items():
            result = client.post("/api/contracts", json={
                **base, "number": f"{kind}-{category}", "property_id": id, "type": kind,
            })
            assert result.status_code == (201 if category in allowed else 422)

    query = client.get("/api/contracts", params={"search": "land_rent-land"}).json()
    lease = f"/api/contracts/{query['items'][0]['id']}"
    land = f"/api/properties/{objects['land']}"
    assert client.patch(lease, json={"property_id": objects["building"]}).status_code == 422
    assert client.patch(lease, json={"type": "real_estate_rent"}).status_code == 422
    assert client.patch(land, json={"type": "building"}).status_code == 409
    assert client.post(f"{lease}/complete").status_code == 200
    assert client.patch(land, json={"type": "building"}).status_code == 200


def test_legacy_archive_and_incompatible_contract_remain_readable(client, engine):
    contract, body = create_agreement(client)
    with Session(engine) as db, db.begin():
        obj = db.get(Property, body["property_id"])
        obj.type = "land"
        obj.status = "archived"
    url = f"/api/contracts/{contract['id']}"
    assert client.get(url).json()["property_archived"]
    assert client.patch(url, json={"note": "Историческая запись"}).status_code == 200
    assert client.post(f"{url}/extend", json={"end_date": day(120)}).status_code == 409
    assert client.patch(f"/api/properties/{body['property_id']}", json={"status": "available"}).status_code == 200
    assert client.post(f"{url}/extend", json={"end_date": day(120)}).status_code == 422
    assert client.patch(url, json={"type": "land_rent"}).status_code == 200
    assert client.post(f"{url}/complete").status_code == 200
    assert client.patch(url, json={"status": "active"}).status_code == 422


def test_accrual_edits_and_payment_boundaries(client):
    first, _ = create_agreement(client)
    second, _ = create_agreement(client, number="ВТОРОЙ")
    accrual = make_accrual(client, first["id"])
    url = f"/api/accruals/{accrual['id']}"
    assert client.patch(url, json={"due_date": day(-100)}).status_code == 422
    assert client.patch(url, json={"contract_id": 99999}).status_code == 404
    result = client.patch(url, json={
        "contract_id": second["id"], "amount": "50", "accrual_date": day(-5),
        "due_date": day(-1), "description": "Исправлено",
    }).json()
    assert (result["contract_number"], result["description"], result["overdue"]) == ("ВТОРОЙ", "Исправлено", "50.00")
    payment = {"amount": "20", "accrual_id": accrual["id"], "payment_date": day()}
    assert client.post("/api/payments", json={**payment, "payment_date": day(1)}).status_code == 422
    assert client.post("/api/payments", json={**payment, "payment_date": day(-6)}).status_code == 422
    future = make_accrual(client, second["id"], accrual_date=day(1), due_date=day(2))
    assert client.post("/api/payments", json={**payment, "accrual_id": future["id"]}).status_code == 422
    assert client.delete(f"/api/accruals/{future['id']}").status_code == 204
    create(client, "payments", payment)
    assert Decimal(chart(client)["kpis"]["debt"]) == 30
    assert client.patch(url, json={"amount": "120"}).status_code == 409
    assert client.delete(url).status_code == 409
    assert client.post("/api/payments", json={**payment, "amount": "31"}).status_code == 409
    create(client, "payments", {**payment, "amount": "30"})
    state = client.get(url).json()
    assert state["paid"] == "50.00" and state["has_payments"]
    assert Decimal(chart(client)["kpis"]["debt"]) == 0


def concurrent(first, second):
    gate = Barrier(2)
    def run(request):
        gate.wait(timeout=5)
        return request()
    with ThreadPoolExecutor(max_workers=2) as workers:
        a = workers.submit(run, first)
        b = workers.submit(run, second)
        return a.result(timeout=10).status_code, b.result(timeout=10).status_code


def test_archive_serializes_with_contract_creation(client):
    prop = create(client, "properties", PROPERTY)
    counterparty = create(client, "counterparties", {"name": "Контрагент"})
    property_url = f"/api/properties/{prop['id']}"
    statuses = concurrent(
        lambda: client.patch(property_url, json={"status": "archived"}),
        lambda: client.post("/api/contracts", json={**CONTRACT, "property_id": prop["id"],
                                                     "counterparty_id": counterparty["id"]}),
    )
    assert statuses in {(200, 409), (409, 201)}


@pytest.mark.parametrize("method", ["PATCH", "DELETE"])
def test_accrual_mutation_serializes_with_payment(client, method):
    contract, _ = create_agreement(client)
    accrual = make_accrual(client, contract["id"], amount="100")
    payment = {"accrual_id": accrual["id"], "payment_date": day(), "amount": "100"}
    url = f"/api/accruals/{accrual['id']}"
    request = (lambda: client.patch(url, json={"amount": "10"})) if method == "PATCH" else (lambda: client.delete(url))
    outcomes = {(409, 201), (200, 409) if method == "PATCH" else (204, 404)}
    assert concurrent(request, lambda: client.post("/api/payments", json=payment)) in outcomes


def test_seed_applies_business_rules(client, engine):
    with Session(engine) as db, db.begin():
        populate(db, AS_OF)
    with Session(engine) as db:
        properties = {row.id: row for row in db.scalars(select(Property))}
        used = set()
        for contract, state in db.execute(select(Contract, registry.contract_state(AS_OF))):
            prop = properties[contract.property_id]
            assert registry.compatible(contract.type, prop.type)
            if state in registry.OPEN_STATES:
                assert prop.status != "archived"
            if state == "active" and contract.type in registry.RENTAL_OBJECT_TYPES:
                used.add(prop.id)
        assert used == {row.id for row in properties.values() if row.status == "in_use"}
        for payment, date in db.execute(select(Payment, Accrual.accrual_date).join(Accrual)):
            assert date <= payment.payment_date <= AS_OF
    for state in ("active", "pending", "completed", "terminated", "expired"):
        assert client.get("/api/contracts", params={"state": state}).json()["total"] > 0
    metrics = chart(client)
    assert all(item["value"] > 0 for item in metrics["panels"]["renewals"]["structure"])
