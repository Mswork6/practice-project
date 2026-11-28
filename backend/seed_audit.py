import re
from calendar import monthrange
from collections import Counter, defaultdict
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Contract, ContractStatus, ContractType, PropertyStatus, PropertyType
from .seed import TABLES
from .services.dashboard import calculate, months_between, shift_month
from .services.registry import contract_state

MARKERS = re.compile(r"демо|demo|тест|\btest\b|synthetic|синтетическ", re.IGNORECASE)
REFERENCE = re.compile(r"На основе договора № ([\w/-]+)\.")


def expected_state(contract, today):
    if contract.status != ContractStatus.ACTIVE:
        return contract.status.value
    if contract.start_date > today:
        return "pending"
    if contract.end_date is not None and contract.end_date < today:
        return "expired"
    return "active"


def audit(db: Session, today):
    properties, parties, contracts, accruals, payments = (
        {row.id: row for row in db.scalars(select(model).order_by(model.id))} for model in TABLES
    )
    checks = []

    def require(condition, description):
        if not condition:
            raise ValueError(f"Не пройдена проверка наполнения: {description}")
        checks.append(description)

    require(all(c.property_id in properties and c.counterparty_id in parties for c in contracts.values())
            and all(a.contract_id in contracts for a in accruals.values())
            and all(p.accrual_id in accruals for p in payments.values()), "все внешние ссылки разрешаются")

    property_types = Counter(p.type.value for p in properties.values())
    property_statuses = Counter(p.status.value for p in properties.values())
    contract_types = Counter(c.type.value for c in contracts.values())
    states = {c.id: expected_state(c, today) for c in contracts.values()}
    state_counts = Counter(states.values())
    require(dict(db.execute(select(Contract.id, contract_state(today))).all()) == states,
            "состояния договоров совпадают с независимым расчётом")
    require(set(property_types) == set(PropertyType) and set(property_statuses) == set(PropertyStatus)
            and set(contract_types) == set(ContractType)
            and set(state_counts) == {"pending", "active", "expired", "completed", "terminated"},
            "представлены все категории объектов, статусы объектов, типы и состояния договоров")

    by_number = {c.number: c for c in contracts.values()}
    require(len(by_number) == len(contracts), "номера договоров уникальны")
    allowed_rent = {
        ContractType.LAND_RENT: (PropertyType.LAND,),
        ContractType.REAL_ESTATE_RENT: (PropertyType.BUILDING, PropertyType.PREMISES),
    }
    active_rent = Counter()
    monthly_ends = Counter()
    buckets = [0] * 4
    references = 0
    early_closed = set()
    for c in contracts.values():
        prop = properties[c.property_id]
        state = states[c.id]
        if c.type in allowed_rent and prop.type not in allowed_rent[c.type]:
            raise ValueError(f"Аудит наполнения: несовместимый договор {c.number}")
        if state in {"active", "pending"} and prop.status == PropertyStatus.ARCHIVED:
            raise ValueError(f"Аудит наполнения: незакрытый договор {c.number} на архивном объекте")
        if c.start_date < prop.created_at or (c.end_date is None and c.type != ContractType.SALE):
            raise ValueError(f"Аудит наполнения: несогласованные данные договора {c.number}")
        if state == "active":
            if c.type in allowed_rent:
                active_rent[c.property_id] += 1
            if c.end_date is not None:
                remaining = (c.end_date - today).days
                buckets[sum(remaining > limit for limit in (30, 90, 180))] += 1
                monthly_ends[shift_month(c.end_date, 0)] += 1
        if state in {"completed", "terminated"} and c.end_date and c.end_date > today:
            early_closed.add(state)
        if c.note:
            if "расторг" in c.note.casefold() and state != "terminated":
                raise ValueError(f"Аудит наполнения: примечание о расторжении у {c.number}")
            if "истёк" in c.note.casefold() and state != "expired":
                raise ValueError(f"Аудит наполнения: примечание о сроке у {c.number}")
            if match := REFERENCE.search(c.note):
                old = by_number.get(match[1])
                if old is None or old.property_id != c.property_id or old.end_date is None or old.end_date >= c.start_date:
                    raise ValueError(f"Аудит наполнения: некорректная ссылка у {c.number}")
                references += 1
    require(all(p.status == (PropertyStatus.ARCHIVED if p.status == PropertyStatus.ARCHIVED else
                PropertyStatus.IN_USE if active_rent[p.id] else PropertyStatus.AVAILABLE)
                for p in properties.values()), "статусы объектов соответствуют действующей аренде")
    require(all(buckets) and max(active_rent.values(), default=0) > 1 and references >= 3
            and early_closed == {"completed", "terminated"},
            "есть все интервалы сроков, параллельная аренда, договоры с перерывами и досрочное закрытие")
    require(any(c.type == ContractType.SALE and c.end_date is None for c in contracts.values()),
            "есть договоры продажи без срока окончания")

    received = defaultdict(list)
    billed = defaultdict(Decimal)
    for p in payments.values():
        a = accruals[p.accrual_id]
        if not a.accrual_date <= p.payment_date <= today or p.amount <= 0:
            raise ValueError(f"Аудит наполнения: некорректный платёж № {p.id}")
        received[a.id].append(p)
    balances = Counter()
    debt_type = defaultdict(Decimal)
    debt_states = Counter()
    for a in accruals.values():
        c = contracts[a.contract_id]
        if (not c.start_date <= a.accrual_date <= min(c.end_date or today, today)
                or a.due_date < a.accrual_date or a.amount <= 0):
            raise ValueError(f"Аудит наполнения: некорректное начисление № {a.id}")
        billed[c.id] += a.amount
        paid = sum((p.amount for p in received[a.id]), Decimal(0))
        if paid > a.amount:
            raise ValueError(f"Аудит наполнения: переплата по начислению № {a.id}")
        balances["full" if paid == a.amount else "partial" if paid else "unpaid"] += 1
        if a.due_date < today and paid < a.amount:
            balances["overdue"] += 1
            debt_type[c.type] += a.amount - paid
            debt_states[states[c.id]] += 1
        if a.due_date >= today and paid < a.amount:
            balances["not_due"] += 1
        if len(received[a.id]) > 1:
            balances["multiple_payments"] += 1
    require(all(total <= contracts[id].amount for id, total in billed.items()),
            "начисления укладываются в суммы договоров, даты и оплаты допустимы")
    require(len(balances) == 6 and set(debt_type) == set(ContractType)
            and debt_states["completed"] > 0 and debt_states["terminated"] > 0,
            "есть все виды оплаты и задолженность по всем типам, включая закрытые договоры")
    by_year = Counter(p.payment_date.year for p in payments.values())
    require(len(by_year) >= 2 and len({(p.payment_date.year, p.payment_date.month) for p in payments.values()}) >= 12,
            "поступления представлены в нескольких годах и минимум в 12 месяцах")

    notes = {
        "properties": sum(bool(p.note) for p in properties.values()),
        "contracts": sum(bool(c.note) for c in contracts.values()),
        "accruals": sum(bool(a.description) for a in accruals.values()),
    }
    require(all(.30 <= count / total <= .50 for count, total in
                zip(notes.values(), (len(properties), len(contracts), len(accruals)))),
            "примечания и описания заполнены у 30–50% строк каждой сущности")
    field_values = (
        [(p.name, p.address, p.note) for p in properties.values()]
        + [(p.name, p.email) for p in parties.values()]
        + [(c.number, c.note) for c in contracts.values()]
        + [(a.description,) for a in accruals.values()]
    )
    require(not any(MARKERS.search(value) for fields in field_values for value in fields if value),
            "в пользовательском тексте отсутствуют маркеры тестового наполнения")

    dashboard = None
    active_types = Counter(p.type for p in properties.values() if p.status != PropertyStatus.ARCHIVED)
    monthly_properties = Counter(shift_month(p.created_at, 0) for p in properties.values())
    for period, year in (("last12", None), ("year", today.year), ("all", None)):
        result = calculate(db, period, year, today)
        if period == "last12":
            dashboard = result
        start = result.period_start
        income_types = defaultdict(Decimal)
        for p in payments.values():
            if start <= p.payment_date <= today:
                income_types[contracts[accruals[p.accrual_id].contract_id].type] += p.amount
        require(result.kpis.income == sum(income_types.values()) and result.kpis.debt == sum(debt_type.values())
                and result.kpis.properties == len(properties) - property_statuses["archived"]
                and result.kpis.renewals == sum(buckets[:3])
                and [p.value for p in result.panels["renewals"].structure] == buckets
                and [p.value for p in result.panels["income"].structure] == [income_types[k] for k in ContractType]
                and [p.value for p in result.panels["properties"].structure] == [active_types[k] for k in PropertyType]
                and [p.value for p in result.panels["debt"].structure] == [debt_type[k] for k in ContractType],
                f"четыре KPI и распределения сходятся с исходными строками для периода {period}")
        months = months_between(start, today)
        month_income = defaultdict(Decimal)
        for p in payments.values():
            if start <= p.payment_date <= today:
                month_income[shift_month(p.payment_date, 0)] += p.amount
        history = []
        for month in months:
            cutoff = min(today, month.replace(day=monthrange(month.year, month.month)[1]))
            history.append(sum((
                a.amount - sum(p.amount for p in received[a.id] if p.payment_date <= cutoff)
                for a in accruals.values() if a.due_date < cutoff
            ), Decimal(0)))
        require([p.value for p in result.panels["income"].trend] == [month_income[m] for m in months]
                and [p.value for p in result.panels["debt"].trend] == history,
                f"помесячные доходы и историческая задолженность сверены для периода {period}")

    require([p.value for p in dashboard.panels["properties"].trend] ==
            [monthly_properties[m] for m in months_between(shift_month(today, -11), today)]
            and [p.value for p in dashboard.panels["renewals"].trend] ==
            [monthly_ends[m] for m in months_between(today, dashboard.contract_end)],
            "помесячное пополнение реестра и окончания договоров сверены")

    return {
        "as_of": today.isoformat(),
        "counts": dict(zip((model.__tablename__ for model in TABLES),
                           map(len, (properties, parties, contracts, accruals, payments)))),
        "property_types": dict(property_types),
        "property_statuses": dict(property_statuses),
        "contract_types": dict(contract_types),
        "contract_states": dict(state_counts),
        "accruals": dict(balances),
        "notes": notes,
        "term_buckets": buckets,
        "payments_by_year": dict(by_year),
        "kpis": dashboard.kpis.model_dump(mode="json"),
        "checks": checks,
    }
