from fastapi import APIRouter, HTTPException, Response
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import aliased

from ..config import local_today
from ..database import Db, commit
from ..models import Accrual, Contract, Payment, Property
from ..schemas import AccrualIn, AccrualOut, AccrualPatch, BalanceStatus, Id, Page
from ..schemas import PaymentDetail, PaymentIn, PaymentOut, PaymentPatch
from ..services.finance import ZERO, lock_payment, lock_unpaid_accrual, register_payment, validate_payment
from .common import PageNumber, PageSize, Search, merge, paginate, require

router = APIRouter(tags=["Финансы"])
accrual_constraints = {
    "accruals_contract_id_fkey": (409, "Выбранный договор стал недоступен.", "contract_id"),
}


def accrual_query(today):
    totals = (
        select(
            Payment.accrual_id,
            func.sum(Payment.amount).label("total"),
            func.sum(Payment.amount).filter(Payment.payment_date <= today).label("received"),
        )
        .group_by(Payment.accrual_id)
        .subquery()
    )
    paid = func.coalesce(totals.c.received, ZERO)
    remaining = func.greatest(ZERO, Accrual.amount - paid)
    overdue = case((Accrual.due_date < today, remaining), else_=ZERO)
    limit = func.greatest(ZERO, Accrual.amount - func.coalesce(totals.c.total, ZERO))
    query = (
        select(Accrual, Contract.number, Property.name, paid, remaining, overdue, totals.c.total.is_not(None), limit)
        .join(Contract, Accrual.contract_id == Contract.id)
        .join(Property, Contract.property_id == Property.id)
        .outerjoin(totals, totals.c.accrual_id == Accrual.id)
    )
    return query, {"paid": remaining == ZERO, "open": remaining > ZERO, "overdue": overdue > ZERO}


def accrual_view(record, number, name, paid, remaining, overdue, has_payments, payment_limit):
    return AccrualOut(
        **AccrualIn.model_validate(record).model_dump(), id=record.id,
        contract_number=number, property_name=name, paid=paid, remaining=remaining, overdue=overdue,
        has_payments=has_payments, payment_limit=payment_limit,
    )


@router.get("/accruals", response_model=Page[AccrualOut])
def accruals(
    db: Db, contract_id: Id | None = None, status: BalanceStatus | None = None, search: Search = "",
    page: PageNumber = 1, page_size: PageSize = 20,
):
    query, filters = accrual_query(local_today())
    if contract_id is not None:
        query = query.where(Accrual.contract_id == contract_id)
    if status is not None:
        query = query.where(filters[status])
    if term := search.strip():
        query = query.where(or_(
            Contract.number.icontains(term, autoescape=True), Property.name.icontains(term, autoescape=True),
        ))
    return paginate(db, query.order_by(Accrual.id.desc()), page, page_size, lambda row: accrual_view(*row))


@router.get("/accruals/{id}", response_model=AccrualOut)
def accrual_detail(id: Id, db: Db):
    query, _ = accrual_query(local_today())
    row = db.execute(query.where(Accrual.id == id)).one_or_none()
    if row is None:
        raise HTTPException(404, "Начисление не найдено.")
    return accrual_view(*row)


@router.post("/accruals", response_model=AccrualOut, status_code=201)
def create_accrual(data: AccrualIn, db: Db):
    require(db, Contract, data.contract_id, "Договор не найден.", field="contract_id")
    row = Accrual(**data.model_dump())
    db.add(row)
    commit(db, "Договор стал недоступен во время сохранения начисления.", constraints=accrual_constraints)
    return accrual_detail(row.id, db)


@router.patch("/accruals/{id}", response_model=AccrualOut)
def update_accrual(id: Id, data: AccrualPatch, db: Db):
    row = lock_unpaid_accrual(db, id)
    values = merge(AccrualIn, row, data)
    require(db, Contract, values["contract_id"], "Договор не найден.", field="contract_id")
    for key, value in values.items():
        setattr(row, key, value)
    commit(db, "Не удалось изменить начисление из-за изменения связанных записей.", constraints=accrual_constraints)
    return accrual_detail(id, db)


@router.delete("/accruals/{id}", status_code=204)
def delete_accrual(id: Id, db: Db):
    db.delete(lock_unpaid_accrual(db, id))
    commit(db, "Начисление с платежами нельзя удалить.")
    return Response(status_code=204)


@router.get("/payments", response_model=Page[PaymentOut])
def payments(db: Db, accrual_id: Id | None = None, page: PageNumber = 1, page_size: PageSize = 20):
    query = select(Payment)
    if accrual_id is not None:
        query = query.where(Payment.accrual_id == accrual_id)
    return paginate(
        db, query.order_by(Payment.payment_date.desc(), Payment.id.desc()), page, page_size, lambda row: row[0],
    )


@router.post("/payments", response_model=PaymentOut, status_code=201)
def create_payment(data: PaymentIn, db: Db):
    return register_payment(db, data, local_today())


@router.get("/payments/{id}", response_model=PaymentDetail)
def payment_detail(id: Id, db: Db):
    other = aliased(Payment)
    other_paid = (
        select(func.coalesce(func.sum(other.amount), ZERO))
        .where(other.accrual_id == Payment.accrual_id, other.id != Payment.id)
        .correlate(Payment)
        .scalar_subquery()
    )
    query = (
        select(Payment, Contract.number, Accrual.accrual_date, func.greatest(ZERO, Accrual.amount - other_paid))
        .join(Accrual, Payment.accrual_id == Accrual.id)
        .join(Contract, Accrual.contract_id == Contract.id)
        .where(Payment.id == id)
    )
    row = db.execute(query).one_or_none()
    if row is None:
        raise HTTPException(404, "Платёж не найден.")
    payment, number, day, limit = row
    return PaymentDetail(
        **PaymentOut.model_validate(payment).model_dump(), contract_number=number, accrual_date=day, payment_limit=limit,
    )


@router.patch("/payments/{id}", response_model=PaymentOut)
def update_payment(id: Id, data: PaymentPatch, db: Db):
    accrual, payment = lock_payment(db, id)
    values = merge(PaymentIn, payment, data)
    validate_payment(db, accrual, values["amount"], values["payment_date"], local_today(), excluding=id)
    payment.amount = values["amount"]
    payment.payment_date = values["payment_date"]
    commit(db, "Платёж не удалось обновить.")
    return payment


@router.delete("/payments/{id}", status_code=204)
def delete_payment(id: Id, db: Db):
    _, payment = lock_payment(db, id)
    db.delete(payment)
    commit(db, "Платёж не удалось удалить.")
    return Response(status_code=204)
