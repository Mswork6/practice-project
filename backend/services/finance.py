from datetime import date
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import commit
from ..models import Accrual, Payment
from ..schemas import PaymentIn

ZERO = Decimal("0.00")


def payments_through(cutoff=None, *correlates):
    query = select(func.coalesce(func.sum(Payment.amount), ZERO)).where(Payment.accrual_id == Accrual.id)
    if cutoff is not None:
        query = query.where(Payment.payment_date <= cutoff)
    return query.correlate(Accrual, *correlates).scalar_subquery()


def overdue_balance(cutoff, *correlates):
    return func.greatest(ZERO, Accrual.amount - payments_through(cutoff, *correlates))


def lock_accrual(db: Session, id: int):
    # Payment, edit and delete all lock the same row before querying its payments.
    accrual = db.get(Accrual, id, with_for_update=True)
    if accrual is None:
        raise HTTPException(404, "Начисление не найдено.")
    return accrual


def lock_unpaid_accrual(db: Session, id: int):
    accrual = lock_accrual(db, id)
    if db.scalar(select(Payment.id).where(Payment.accrual_id == id).limit(1)) is not None:
        raise HTTPException(409, "Начисление с зарегистрированными платежами нельзя изменить или удалить.")
    return accrual


def register_payment(db: Session, data: PaymentIn, today: date):
    accrual = lock_accrual(db, data.accrual_id)
    if data.payment_date > today:
        raise HTTPException(422, "Дата платежа не может быть позже сегодняшней даты.")
    if data.payment_date < accrual.accrual_date:
        raise HTTPException(422, "Дата платежа не может предшествовать дате начисления.")
    paid = db.scalar(select(func.sum(Payment.amount)).where(Payment.accrual_id == accrual.id)) or ZERO
    if paid + data.amount > accrual.amount:
        raise HTTPException(409, f"Платёж превышает остаток начисления ({accrual.amount - paid:.2f} ₽).")
    record = Payment(**data.model_dump())
    db.add(record)
    commit(db, "Начисление стало недоступно во время регистрации платежа.")
    return record
