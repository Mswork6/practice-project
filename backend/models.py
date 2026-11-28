from datetime import date
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import CheckConstraint, Date, Enum, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .config import local_today


class PropertyType(StrEnum):
    BUILDING = "building"
    LAND = "land"
    PREMISES = "premises"
    OTHER = "other"


class PropertyStatus(StrEnum):
    AVAILABLE = "available"
    IN_USE = "in_use"
    ARCHIVED = "archived"


class ContractType(StrEnum):
    REAL_ESTATE_RENT = "real_estate_rent"
    LAND_RENT = "land_rent"
    SALE = "sale"
    OTHER = "other"


class ContractStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    TERMINATED = "terminated"


class Base(DeclarativeBase):
    pass


def choices(values):
    return Enum(
        values,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda members: [member.value for member in members],
    )


class Property(Base):
    __tablename__ = "properties"
    __table_args__ = (CheckConstraint("area > 0", name="positive_property_area"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    type: Mapped[PropertyType] = mapped_column(choices(PropertyType), index=True)
    address: Mapped[str] = mapped_column(String(300))
    area: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    cadastral_number: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[PropertyStatus] = mapped_column(choices(PropertyStatus), index=True)
    created_at: Mapped[date] = mapped_column(Date, default=local_today, index=True)
    note: Mapped[str | None] = mapped_column(Text)


class Counterparty(Base):
    __tablename__ = "counterparties"
    __table_args__ = (
        UniqueConstraint("tax_id", name="unique_counterparty_tax_id"),
        CheckConstraint("tax_id ~ '^([0-9]{10}|[0-9]{12})$'", name="valid_counterparty_tax_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    tax_id: Mapped[str] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(250))


class Contract(Base):
    __tablename__ = "contracts"
    __table_args__ = (
        CheckConstraint("end_date >= start_date", name="contract_dates_in_order"),
        CheckConstraint("end_date IS NOT NULL OR type = 'sale'", name="contract_end_required"),
        CheckConstraint("amount > 0", name="positive_contract_amount"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(80), unique=True)
    type: Mapped[ContractType] = mapped_column(choices(ContractType), index=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id", ondelete="RESTRICT"), index=True)
    counterparty_id: Mapped[int] = mapped_column(ForeignKey("counterparties.id", ondelete="RESTRICT"), index=True)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date, index=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    status: Mapped[ContractStatus] = mapped_column(choices(ContractStatus), index=True)
    note: Mapped[str | None] = mapped_column(Text)


class Accrual(Base):
    __tablename__ = "accruals"
    __table_args__ = (
        CheckConstraint("amount > 0", name="positive_accrual_amount"),
        CheckConstraint("due_date >= accrual_date", name="accrual_dates_in_order"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    contract_id: Mapped[int] = mapped_column(ForeignKey("contracts.id", ondelete="RESTRICT"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    accrual_date: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date] = mapped_column(Date, index=True)
    description: Mapped[str | None] = mapped_column(Text)


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (CheckConstraint("amount > 0", name="positive_payment_amount"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    accrual_id: Mapped[int] = mapped_column(ForeignKey("accruals.id", ondelete="RESTRICT"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    payment_date: Mapped[date] = mapped_column(Date, index=True)
