from decimal import Decimal

import pytest

from test_application import assert_payment, chart, create, create_agreement, make_accrual
from test_business_rules import concurrent, day


def contract_path(contract):
    return f"/api/contracts/{contract['id']}"


def property_status(client, agreement):
    return client.get(f"/api/properties/{agreement['property_id']}").json()["status"]


@pytest.mark.parametrize("status", ["completed", "terminated"])
def test_create_rejects_explicit_closed_status(client, status):
    contract, body = create_agreement(client)
    assert contract["status"] == "active"
    result = client.post("/api/contracts", json={**body, "number": "НОВЫЙ", "status": status})
    assert result.status_code == 422
    assert client.get("/api/contracts").json()["total"] == 1


@pytest.mark.parametrize("state,start,end,allowed", [
    ("pending", 1, 10, set()),
    ("active", -1, 10, {"complete", "terminate", "extend"}),
    ("expired", -10, -2, {"complete", "successor"}),
    ("completed", -1, 10, {"successor"}),
    ("terminated", -1, 10, {"successor"}),
])
def test_allowed_lifecycle_actions(client, state, start, end, allowed):
    for action in ("complete", "terminate", "extend", "successor"):
        contract, body = create_agreement(client, number=action, start_date=day(start), end_date=day(end))
        url = contract_path(contract)
        if state in ("completed", "terminated"):
            path = "complete" if state == "completed" else "terminate"
            assert client.post(f"{url}/{path}").json()["state"] == state
        else:
            assert contract["state"] == state and contract["status"] == "active"
        payload = {"end_date": day(40)} if action == "extend" else None
        if action == "successor":
            # An administrative closure can precede the original planned end date.
            payload = {**body, "number": f"НОВЫЙ-{state}", "start_date": day(-1), "end_date": day(40)}
        result = client.post(f"{url}/{action}", json=payload)
        expected = (201 if action == "successor" else 200) if action in allowed else 409
        assert result.status_code == expected, result.json()
        if action == "successor" and action in allowed:
            previous = client.get(url).json()
            assert previous["state"] == ("completed" if state == "expired" else state)
            assert result.json()["status"] == "active"


@pytest.mark.parametrize("operation", ["complete", "terminate"])
def test_closure_preserves_dates_and_payable_debt(client, operation):
    agreement, _ = create_agreement(client)
    accrual = make_accrual(client, agreement["id"], amount="100")
    payload = {"accrual_id": accrual["id"], "amount": "25.00", "payment_date": day()}
    first_payment = create(client, "payments", payload)
    assert property_status(client, agreement) == "in_use" and chart(client)["kpis"]["renewals"] == 1

    url = contract_path(agreement)
    result = client.post(f"{url}/{operation}")
    assert result.status_code == 200
    state = "completed" if operation == "complete" else "terminated"
    assert result.json() == {**agreement, "status": state, "state": state}
    assert property_status(client, agreement) == "available"
    assert chart(client)["kpis"]["renewals"] == 0
    assert Decimal(chart(client)["kpis"]["debt"]) == 75
    assert_payment(client, first_payment)
    create(client, "payments", {**payload, "amount": "75"})
    assert Decimal(chart(client)["kpis"]["debt"]) == 0


def test_extend_changes_only_end_date_and_updates_dashboard(client):
    agreement, _ = create_agreement(client, note="Примечание")
    accrual = make_accrual(client, agreement["id"], amount="100")
    payment = create(client, "payments", {"accrual_id": accrual["id"], "amount": "25.00", "payment_date": day()})
    url = contract_path(agreement)
    for end in (day(19), day(20)):
        assert client.post(f"{url}/extend", json={"end_date": end}).status_code == 422
    assert client.post(f"{url}/extend", json={"end_date": day(220), "amount": "1"}).status_code == 422

    result = client.post(f"{url}/extend", json={"end_date": day(220)})
    assert result.status_code == 200
    assert result.json() == {**agreement, "end_date": day(220), "days_remaining": 220}
    assert client.get(f"/api/accruals/{accrual['id']}").json()["contract_id"] == agreement["id"]
    assert_payment(client, payment)
    metrics = chart(client)
    assert metrics["kpis"]["renewals"] == 0
    assert metrics["panels"]["renewals"]["structure"][-1]["value"] == 1
    assert property_status(client, agreement) == "in_use"


@pytest.mark.parametrize("kind", ["sale", "other"])
def test_cannot_extend_other_active_contract_types(client, kind):
    agreement, _ = create_agreement(client, type=kind)
    assert client.post(f"{contract_path(agreement)}/extend", json={"end_date": day(50)}).status_code == 409


def test_successor_is_atomic_and_preserves_earlier_history(client):
    old, body = create_agreement(client, start_date=day(-60), end_date=day(-10), note="Старая запись")
    accrual = make_accrual(client, old["id"], amount="100", accrual_date=day(-30), due_date=day(-20))
    payment = create(client, "payments", {"accrual_id": accrual["id"], "amount": "25.00", "payment_date": day(-5)})
    url = contract_path(old)
    successor = {**body, "number": "ПРОДОЛЖЕНИЕ", "start_date": day(-9), "end_date": day(60), "note": "Новая сделка"}
    for invalid, code in [({"start_date": day(-10)}, 422), ({"number": old["number"]}, 409),
                          ({"property_id": 9999}, 404), ({"note": "А" * 3000}, 422)]:
        result = client.post(f"{url}/successor", json={**successor, **invalid})
        assert result.status_code == code
        detail = client.get(url).json()
        assert {key: detail[key] for key in old} == old
    assert client.get("/api/contracts").json()["total"] == 1

    result = client.post(f"{url}/successor", json=successor)
    assert result.status_code == 201
    new = result.json()
    assert new["id"] != old["id"] and new["state"] == "active"
    assert new["note"] == f"На основе договора № {old['number']}.\nНовая сделка"
    detail = client.get(url).json()
    assert {key: detail[key] for key in old} == {**old, "status": "completed", "state": "completed"}
    assert client.get(f"/api/accruals?contract_id={new['id']}").json()["total"] == 0
    assert_payment(client, payment)
    assert Decimal(chart(client)["kpis"]["debt"]) == 75
    assert property_status(client, new) == "in_use"
    assert chart(client)["kpis"]["renewals"] == 1


def test_patch_cannot_bypass_lifecycle(client):
    contract, body = create_agreement(client)
    url = contract_path(contract)
    for change in ({"end_date": day(21)}, {"start_date": day(1)}, {"end_date": day(-1)},
                   {"type": "sale", "end_date": None}):
        assert client.patch(url, json=change).status_code == 409
    assert client.patch(url, json={"status": "completed"}).status_code == 422
    assert client.patch(url, json={"end_date": day(10)}).json()["end_date"] == day(10)

    pending = create(client, "contracts", {**body, "number":"ПЛАН", "start_date":day(10)})
    assert client.patch(contract_path(pending), json={"start_date": day(5), "end_date": day(60)}).json()["state"] == "pending"

    ended = create(client, "contracts", {**body, "number": "ИСТЁК", "end_date": day(-1)})
    ended_url = contract_path(ended)
    for change in ({"end_date": day(30)}, {"start_date": day(-6)}, {"amount": "1"}, {"number": "НОВЫЙ"},
                   {"type": "sale"}, {"counterparty_id": 4}, {"property_id": 4}):
        assert client.patch(ended_url, json=change).status_code == 409
    assert client.patch(ended_url, json={"note": "Уточнено"}).json() == {**ended, "note": "Уточнено"}
    assert client.post(f"{ended_url}/complete").json()["state"] == "completed"
    assert client.patch(ended_url, json={"note": "Правка"}).status_code == 409
    assert client.patch(ended_url, json={"status": "active"}).status_code == 422


def test_closure_actions_serialize_on_agreement(client):
    agreement, _ = create_agreement(client)
    url = contract_path(agreement)
    outcomes = concurrent(lambda: client.post(f"{url}/terminate"), lambda: client.post(f"{url}/complete"))
    assert sorted(outcomes) == [200, 409]
    assert client.get(url).json()["state"] in {"completed", "terminated"}
