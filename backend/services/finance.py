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


def register_payment(db: Session, data: PaymentIn):
    # Every writer locks the same accrual before reading its payments. At READ COMMITTED
    # the following statement sees payments committed while this transaction waited.
    accrual = db.scalar(select(Accrual).where(Accrual.id == data.accrual_id).with_for_update())
    if accrual is None:
        raise HTTPException(404, "Начисление не найдено.")
    if data.payment_date < accrual.accrual_date:
        raise HTTPException(422, "Дата платежа не может предшествовать дате начисления.")
    paid = db.scalar(select(func.sum(Payment.amount)).where(Payment.accrual_id == accrual.id)) or ZERO
    if paid + data.amount > accrual.amount:
        raise HTTPException(409, f"Платёж превышает остаток начисления ({accrual.amount - paid:.2f} ₽).")
    record = Payment(**data.model_dump())
    db.add(record)
    commit(db, "Начисление стало недоступно во время регистрации платежа.")
    return record
