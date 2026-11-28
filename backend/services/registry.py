from enum import StrEnum

from sqlalchemy import case, select

from ..models import Contract, ContractStatus, ContractType, Property, PropertyStatus, PropertyType


class ContractState(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    EXPIRED = "expired"
    COMPLETED = "completed"
    TERMINATED = "terminated"


RENTAL_OBJECT_TYPES = {
    ContractType.LAND_RENT: (PropertyType.LAND,),
    ContractType.REAL_ESTATE_RENT: (PropertyType.BUILDING, PropertyType.PREMISES),
}
OPEN_STATES = (ContractState.ACTIVE, ContractState.PENDING)
TERM_RANGES = {"0-30": (0, 30), "31-90": (31, 90), "91-180": (91, 180), "181+": (181, None)}


def remaining_term(today, term):
    lower, upper = TERM_RANGES[term]
    days = Contract.end_date - today
    return days.between(lower, upper) if upper is not None else days >= lower


def compatible(contract_type, property_type):
    permitted = RENTAL_OBJECT_TYPES.get(contract_type)
    return permitted is None or property_type in permitted


def contract_state(today, status=Contract.status, start=Contract.start_date, end=Contract.end_date):
    # One SQL expression serves rows, filters, analytics and proposed changes.
    return case(
        (status == ContractStatus.COMPLETED, ContractState.COMPLETED.value),
        (status == ContractStatus.TERMINATED, ContractState.TERMINATED.value),
        (start > today, ContractState.PENDING.value),
        (end < today, ContractState.EXPIRED.value),
        else_=ContractState.ACTIVE.value,
    )


def property_contracts(today):
    return select(Contract.id).where(
        Contract.property_id == Property.id,
        contract_state(today).in_(OPEN_STATES),
    ).correlate(Property).exists()


def property_status(today):
    active_rent = (
        select(Contract.id)
        .where(
            Contract.property_id == Property.id,
            Contract.type.in_(tuple(RENTAL_OBJECT_TYPES)),
            contract_state(today) == ContractState.ACTIVE,
        )
        .correlate(Property)
        .exists()
    )
    return case(
        (Property.status == PropertyStatus.ARCHIVED, PropertyStatus.ARCHIVED.value),
        (active_rent, PropertyStatus.IN_USE.value),
        else_=PropertyStatus.AVAILABLE.value,
    )
