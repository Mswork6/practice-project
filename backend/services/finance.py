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


def validate_payment(db: Session, accrual: Accrual, amount: Decimal, payment_date: date, today: date, *, excluding=None):
    if payment_date > today:
        raise HTTPException(422, "Дата платежа не может быть позже сегодняшней даты.")
    if payment_date < accrual.accrual_date:
        raise HTTPException(422, "Дата платежа не может предшествовать дате начисления.")
    query = select(func.sum(Payment.amount)).where(Payment.accrual_id == accrual.id)
    if excluding is not None:
        query = query.where(Payment.id != excluding)
    paid = db.scalar(query) or ZERO
    if paid + amount > accrual.amount:
        raise HTTPException(409, f"Платёж превышает остаток начисления ({accrual.amount - paid:.2f} ₽).")


def lock_payment(db: Session, id: int):
    # Read only the immutable parent ID before acquiring the common accrual lock.
    # Re-read the payment afterwards in case another transaction just changed or deleted it.
    accrual_id = db.scalar(select(Payment.accrual_id).where(Payment.id == id))
    if accrual_id is not None:
        accrual = db.get(Accrual, accrual_id, with_for_update=True)
        if accrual is not None:
            payment = db.get(Payment, id)
            if payment is not None:
                return accrual, payment
    raise HTTPException(404, "Платёж не найден.")


def register_payment(db: Session, data: PaymentIn, today: date):
    accrual = lock_accrual(db, data.accrual_id)
    validate_payment(db, accrual, data.amount, data.payment_date, today)
    record = Payment(**data.model_dump())
    db.add(record)
    commit(db, "Начисление стало недоступно во время регистрации платежа.")
    return record
