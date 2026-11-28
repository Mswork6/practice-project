from datetime import date
from decimal import Decimal
import re
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints, field_validator

from .models import ContractStatus, ContractType, PropertyStatus, PropertyType
from .services.counterparty import clean_email, normalize_tax_id, valid_tax_checksum, valid_tax_id
from .services.registry import ContractState
from .services.requisites import normalize_cadastral, valid_cadastral


def decimal_input(value):
    if isinstance(value, str):
        return value.strip().replace(",", ".")
    return value


def check_scale(value):
    if value.as_tuple().exponent < -2:
        raise ValueError("Укажите не более двух знаков после десятичного разделителя.")
    return value


def calendar_input(value):
    if value is not None and type(value) is not date:
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            raise ValueError("Укажите корректную календарную дату.")
    return value


def check_cadastral(value):
    if value is not None and not valid_cadastral(value):
        raise ValueError("Некорректный формат кадастрового номера.")
    return value

Name = Annotated[str, StringConstraints(min_length=1, max_length=200)]
PositiveAmount = Annotated[
    Decimal, Field(gt=0, max_digits=14, decimal_places=2, allow_inf_nan=False),
    BeforeValidator(decimal_input), AfterValidator(check_scale),
]
CalendarDate = Annotated[date, BeforeValidator(calendar_input)]
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
    cadastral_number: Annotated[str | None, BeforeValidator(normalize_cadastral), AfterValidator(check_cadastral)] = None
    status: PropertyStatus | None = None
    note: Annotated[str, StringConstraints(max_length=3000)] | None = None


class PropertyIn(PropertyPatch):
    name: Name
    type: PropertyType
    address: Annotated[str, StringConstraints(min_length=1, max_length=300)]
    status: PropertyStatus = PropertyStatus.AVAILABLE


class PropertyOut(Schema):
    id: Id
    name: Name
    type: PropertyType
    address: str
    area: Decimal | None
    cadastral_number: str | None
    status: PropertyStatus
    note: str | None
    created_at: date
    can_archive: bool


class CounterpartyFields(Schema):
    name: Name
    tax_id: str
    email: str | None = None

    @field_validator("tax_id")
    @classmethod
    def check_tax_id(cls, value):
        value = normalize_tax_id(value)
        if not value:
            raise ValueError("Укажите ИНН контрагента.")
        if not valid_tax_id(value):
            raise ValueError("ИНН должен содержать 10 или 12 цифр.")
        return value

    @field_validator("email")
    @classmethod
    def check_email(cls, value):
        return clean_email(value)


class CounterpartyIn(CounterpartyFields):
    @field_validator("tax_id")
    @classmethod
    def check_checksum(cls, value):
        if not valid_tax_checksum(value):
            raise ValueError("Некорректный ИНН. Проверьте введённые цифры.")
        return value


class CounterpartyPatch(CounterpartyIn):
    name: Name | None = None
    tax_id: str | None = None


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
    start_date: CalendarDate | None = None
    end_date: CalendarDate | None = None
    amount: PositiveAmount | None = None
    note: Annotated[str, StringConstraints(max_length=3000)] | None = None


class ContractIn(ContractPatch):
    number: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    type: ContractType
    property_id: Id
    counterparty_id: Id
    start_date: CalendarDate
    end_date: CalendarDate | None = Field(default=None, validate_default=True)

    @field_validator("end_date")
    @classmethod
    def check_dates(cls, value, info):
        kind, start = info.data.get("type"), info.data.get("start_date")
        if value is None and kind is not None and kind != ContractType.SALE:
            raise ValueError("Укажите дату окончания договора.")
        if value is not None and start is not None and value < start:
            raise ValueError("Дата окончания не может быть раньше даты начала.")
        return value


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
    end_date: CalendarDate


class AccrualPatch(Schema):
    contract_id: Id | None = None
    amount: PositiveAmount | None = None
    accrual_date: CalendarDate | None = None
    due_date: CalendarDate | None = None
    description: Annotated[str, StringConstraints(max_length=3000)] | None = None


class AccrualIn(AccrualPatch):
    contract_id: Id
    amount: PositiveAmount
    accrual_date: CalendarDate
    due_date: CalendarDate

    @field_validator("due_date")
    @classmethod
    def check_dates(cls, value, info):
        if (accrual_date := info.data.get("accrual_date")) is not None and value < accrual_date:
            raise ValueError("Срок оплаты не может предшествовать дате начисления.")
        return value


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
    payment_date: CalendarDate | None = None


class PaymentIn(PaymentPatch):
    accrual_id: Id
    amount: PositiveAmount
    payment_date: CalendarDate


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
