from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator
import re

from .models import ContractStatus, ContractType, PropertyStatus, PropertyType
from .services.registry import ContractState

Name = Annotated[str, StringConstraints(min_length=1, max_length=200)]
PositiveAmount = Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=2, allow_inf_nan=False)]
Id = Annotated[int, Field(gt=0, le=2_147_483_647)]
Period = Literal["last12", "year", "all"]
BalanceStatus = Literal["paid", "open", "overdue"]


class Schema(BaseModel):
    model_config = ConfigDict(from_attributes=True, str_strip_whitespace=True, extra="forbid")


class PropertyPatch(Schema):
    name: Name | None = None
    type: PropertyType | None = None
    address: Annotated[str, StringConstraints(min_length=1, max_length=300)] | None = None
    area: PositiveAmount | None = None
    cadastral_number: Annotated[str, StringConstraints(max_length=80)] | None = None
    status: PropertyStatus | None = None
    note: Annotated[str, StringConstraints(max_length=3000)] | None = None


class PropertyIn(PropertyPatch):
    name: Name
    type: PropertyType
    address: Annotated[str, StringConstraints(min_length=1, max_length=300)]
    status: PropertyStatus = PropertyStatus.AVAILABLE


class PropertyOut(PropertyIn):
    id: Id
    created_at: date
    can_archive: bool


class CounterpartyFields(Schema):
    name: Name
    tax_id: Annotated[str, StringConstraints(max_length=20)] | None = None
    contact: Annotated[str, StringConstraints(max_length=250)] | None = None


class CounterpartyPatch(CounterpartyFields):
    name: Name | None = None

    @field_validator("tax_id", mode="before")
    @classmethod
    def normalize_tax_id(cls, value):
        if isinstance(value, str):
            value = re.sub(r"[\s-]", "", value)
            if value and not re.fullmatch(r"[0-9]{10}|[0-9]{12}", value):
                raise ValueError("ИНН должен содержать 10 или 12 цифр.")
            return value or None
        return value


class CounterpartyIn(CounterpartyPatch):
    name: Name


class CounterpartyOut(CounterpartyFields):
    id: Id
    contract_count: int
    active_contract_count: int


class FinancialTotals(Schema):
    billed: Decimal
    paid: Decimal
    remaining: Decimal
    overdue: Decimal


class CounterpartyDetail(CounterpartyOut):
    finance: FinancialTotals


class ContractPatch(Schema):
    number: Annotated[str, StringConstraints(min_length=1, max_length=80)] | None = None
    type: ContractType | None = None
    property_id: Id | None = None
    counterparty_id: Id | None = None
    start_date: date | None = None
    end_date: date | None = None
    amount: PositiveAmount | None = None
    note: Annotated[str, StringConstraints(max_length=3000)] | None = None


class ContractIn(ContractPatch):
    number: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    type: ContractType
    property_id: Id
    counterparty_id: Id
    start_date: date

    @model_validator(mode="after")
    def check_dates(self):
        if self.end_date is None and self.type != ContractType.SALE:
            raise ValueError("Укажите дату окончания договора.")
        if self.end_date is not None and self.end_date < self.start_date:
            raise ValueError("Дата окончания договора не может предшествовать дате начала.")
        return self


class ContractOut(ContractIn):
    id: Id
    status: ContractStatus
    property_name: str
    counterparty_name: str
    days_remaining: int | None
    state: ContractState
    property_archived: bool


class ContractDetail(ContractOut):
    finance: FinancialTotals


class ContractExtension(Schema):
    end_date: date


class AccrualPatch(Schema):
    contract_id: Id | None = None
    amount: PositiveAmount | None = None
    accrual_date: date | None = None
    due_date: date | None = None
    description: Annotated[str, StringConstraints(max_length=3000)] | None = None


class AccrualIn(AccrualPatch):
    contract_id: Id
    amount: PositiveAmount
    accrual_date: date
    due_date: date

    @model_validator(mode="after")
    def check_dates(self):
        if self.due_date < self.accrual_date:
            raise ValueError("Срок оплаты не может предшествовать дате начисления.")
        return self


class AccrualOut(AccrualIn):
    id: Id
    contract_number: str
    property_name: str
    paid: Decimal
    remaining: Decimal
    overdue: Decimal
    has_payments: bool
    payment_limit: Decimal


class PaymentPatch(Schema):
    amount: PositiveAmount | None = None
    payment_date: date | None = None


class PaymentIn(PaymentPatch):
    accrual_id: Id
    amount: PositiveAmount
    payment_date: date


class PaymentOut(PaymentIn):
    id: Id


class PaymentDetail(PaymentOut):
    contract_number: str
    accrual_date: date
    payment_limit: Decimal


class Page[Item](Schema):
    items: list[Item]
    total: int
    page: int
    page_size: int


class Point(Schema):
    label: str
    value: Decimal | int
    key: str | None = None
    future: bool = False


class Panel(Schema):
    trend: list[Point]
    structure: list[Point]


class KPIs(Schema):
    income: Decimal
    debt: Decimal
    properties: int
    renewals: int


class Dashboard(Schema):
    as_of: date
    period_start: date | None
    period_end: date
    contract_start: date
    contract_end: date
    kpis: KPIs
    panels: dict[str, Panel]
    income_years: list[int] = Field(default_factory=list)
