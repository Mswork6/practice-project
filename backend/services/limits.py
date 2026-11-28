from datetime import date
from decimal import Decimal

from ..validation import FieldError

MAX_AREA = Decimal("1000000000")
AREA_CHECK = "bounded_property_area"
AREA_CHECK_SQL = f"area <= {MAX_AREA}"
AREA_MAX_MESSAGE = "Площадь объекта не может превышать 1 000 000 000 м²."

DATE_FLOOR = date(1900, 1, 1)


def add_years(day: date, years: int) -> date:
    """Add calendar years; February 29 becomes the last day of February."""
    year = min(day.year + years, date.max.year)
    try:
        return day.replace(year=year)
    except ValueError:
        return day.replace(year=year, day=28)


def date_limits(today: date) -> tuple[date, date]:
    """Inclusive upper bounds for near dates and for final dates."""
    return add_years(today, 10), add_years(today, 100)


def check_date(value: date, label: str, ceiling: date, *, floor: date = DATE_FLOOR, field: str) -> None:
    if value < floor:
        raise FieldError(422, f"{label} не может быть ранее {floor:%d.%m.%Y}.", field)
    if value > ceiling:
        raise FieldError(422, f"{label} не может быть позднее {ceiling:%d.%m.%Y}.", field)


def check_contract_dates(start_date, end_date, today: date) -> None:
    soon, distant = date_limits(today)
    check_date(start_date, "Дата начала договора", soon, field="start_date")
    if end_date is not None:
        check_date(end_date, "Дата окончания договора", distant, floor=start_date or DATE_FLOOR, field="end_date")


def check_accrual_dates(accrual_date, due_date, today: date) -> None:
    soon, distant = date_limits(today)
    check_date(accrual_date, "Дата начисления", soon, field="accrual_date")
    check_date(due_date, "Срок оплаты", distant, floor=accrual_date or DATE_FLOOR, field="due_date")
