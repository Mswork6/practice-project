from calendar import monthrange
from datetime import date, timedelta

from sqlalchemy import Date, cast, column, func, select, values
from sqlalchemy.orm import Session

from ..models import Accrual, Contract, ContractStatus, ContractType, Payment, Property, PropertyStatus, PropertyType
from ..schemas import Dashboard, KPIs, Panel, Period, Point
from .finance import ZERO, overdue_balance

MONTHS = ("Янв", "Фев", "Мар", "Апр", "Май", "Июн", "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек")
CONTRACT_LABELS = dict(zip(ContractType, ("Аренда недвижимости", "Аренда земли", "Продажа имущества", "Прочие")))
PROPERTY_LABELS = dict(zip(PropertyType, ("Здания", "Земельные участки", "Нежилые помещения", "Прочие")))
TERM_LABELS = ("0–30 дней", "31–90 дней", "91–180 дней", "181 день и более")


def shift_month(day: date, offset: int) -> date:
    year, month = divmod(day.year * 12 + day.month - 1 + offset, 12)
    return date(year, month + 1, 1)


def months_between(start: date | None, end: date) -> list[date]:
    if start is None:
        return []
    count = (end.year - start.year) * 12 + end.month - start.month + 1
    return [shift_month(start, i) for i in range(count)]


def financial_range(db: Session, period: Period, year: int | None, today: date):
    if period == "year":
        chosen = year or today.year
        return date(chosen, 1, 1), min(today, date(chosen, 12, 31))
    if period == "last12":
        return shift_month(today, -11), today
    first_dates = db.execute(select(
        select(func.min(Payment.payment_date)).where(Payment.payment_date <= today).scalar_subquery(),
        select(func.min(Accrual.accrual_date)).where(Accrual.accrual_date <= today).scalar_subquery(),
    )).one()
    return min((day for day in first_dates if day is not None), default=None), today


def month_key(day):
    return cast(func.date_trunc("month", day), Date)


def trend(months, records, default=0):
    return [Point(label=f"{MONTHS[month.month - 1]} {month.year}", value=records.get(month, default)) for month in months]


def structure(labels, records, default=0):
    return [Point(label=label, value=records.get(key, default)) for key, label in labels.items()]


def income_panel(db, start, end, months):
    window = Payment.payment_date.between(start, end) if start else Payment.payment_date <= end
    month = month_key(Payment.payment_date)
    amounts = dict(db.execute(
        select(month, func.sum(Payment.amount)).where(window).group_by(month)
    ).all())
    by_type = dict(db.execute(
        select(Contract.type, func.sum(Payment.amount))
        .join(Accrual, Payment.accrual_id == Accrual.id)
        .join(Contract, Accrual.contract_id == Contract.id)
        .where(window).group_by(Contract.type)
    ).all())
    return sum(by_type.values(), ZERO), Panel(
        trend=trend(months, amounts, ZERO), structure=structure(CONTRACT_LABELS, by_type, ZERO),
    )


def debt_panel(db, today, end, months):
    by_type = dict(db.execute(
        select(Contract.type, func.sum(overdue_balance(today)))
        .join(Accrual, Accrual.contract_id == Contract.id)
        .where(Accrual.accrual_date <= today, Accrual.due_date < today)
        .group_by(Contract.type)
    ).all())
    history = {}
    if months:
        cutoffs = [
            (month, min(end, date(month.year, month.month, monthrange(month.year, month.month)[1])))
            for month in months
        ]
        snapshots = values(column("month", Date), column("cutoff", Date)).data(cutoffs).cte("snapshots")
        history = dict(db.execute(
            select(snapshots.c.month, func.sum(overdue_balance(snapshots.c.cutoff, snapshots)))
            .select_from(snapshots)
            .join(Accrual, (Accrual.accrual_date <= snapshots.c.cutoff) & (Accrual.due_date < snapshots.c.cutoff))
            .group_by(snapshots.c.month)
        ).all())
    return sum(by_type.values(), ZERO), Panel(
        trend=trend(months, history, ZERO), structure=structure(CONTRACT_LABELS, by_type, ZERO),
    )


def property_panel(db, today):
    months = months_between(shift_month(today, -11), today)
    month = month_key(Property.created_at)
    additions = dict(db.execute(
        select(month, func.count()).where(Property.created_at.between(months[0], today)).group_by(month)
    ).all())
    categories = dict(db.execute(
        select(Property.type, func.count()).where(Property.status != PropertyStatus.ARCHIVED).group_by(Property.type)
    ).all())
    return sum(categories.values()), Panel(
        trend=trend(months, additions), structure=structure(PROPERTY_LABELS, categories),
    )


def contract_panel(db, today):
    end = shift_month(today, 12) - timedelta(days=1)
    month = month_key(Contract.end_date)
    endings = dict(db.execute(
        select(month, func.count()).where(Contract.end_date.between(today, end)).group_by(month)
    ).all())
    remaining = Contract.end_date - today
    intervals = ((0, 30), (31, 90), (91, 180))
    counts = db.execute(
        select(
            *(func.count().filter(remaining.between(first, last)) for first, last in intervals),
            func.count().filter(remaining >= 181),
        ).select_from(Contract).where(Contract.status == ContractStatus.ACTIVE)
    ).one()
    return sum(counts[:3]), end, Panel(
        trend=trend(months_between(today, end), endings),
        structure=[Point(label=label, value=count) for label, count in zip(TERM_LABELS, counts)],
    )


def calculate(db: Session, period: Period, year: int | None, today: date) -> Dashboard:
    start, end = financial_range(db, period, year, today)
    months = months_between(start, end)
    income, income_data = income_panel(db, start, end, months)
    debt, debt_data = debt_panel(db, today, end, months)
    properties, property_data = property_panel(db, today)
    renewals, contract_end, contract_data = contract_panel(db, today)
    return Dashboard(
        as_of=today, period_start=start, period_end=end, contract_start=today, contract_end=contract_end,
        kpis=KPIs(income=income, debt=debt, properties=properties, renewals=renewals),
        panels={"income": income_data, "debt": debt_data, "properties": property_data, "renewals": contract_data},
    )
