from fastapi import APIRouter, HTTPException, Response
from sqlalchemy import Date, literal, or_, select

from ..config import local_today
from ..database import Db, commit
from ..models import Contract, ContractStatus, ContractType, Counterparty, Property, PropertyStatus, PropertyType
from ..schemas import ContractExtension, ContractIn, ContractOut, ContractPatch, CounterpartyIn, CounterpartyOut, Id
from ..schemas import Page, PropertyIn, PropertyOut, PropertyPatch
from ..services.registry import (
    OPEN_STATES, RENTAL_OBJECT_TYPES, ContractState, compatible, contract_state, property_contracts, property_status,
)
from .common import PageNumber, PageSize, Search, merge, paginate, require

router = APIRouter(tags=["Реестры"])


def property_query(today):
    return select(Property, property_status(today), property_contracts(today))


def property_view(record, status, has_open_contracts):
    return PropertyOut(
        **PropertyIn.model_validate(record).model_dump(exclude={"status"}),
        id=record.id, created_at=record.created_at, status=status,
        can_archive=status != PropertyStatus.ARCHIVED and not has_open_contracts,
    )


def load_property(db, id, today):
    row = db.execute(property_query(today).where(Property.id == id)).one_or_none()
    if row is None:
        raise HTTPException(404, "Объект не найден.")
    return property_view(*row)


@router.get("/properties", response_model=Page[PropertyOut])
def properties(
    db: Db, search: Search = "", type: PropertyType | None = None, status: PropertyStatus | None = None,
    exclude_archived: bool = False, contract_type: ContractType | None = None,
    page: PageNumber = 1, page_size: PageSize = 20,
):
    today = local_today()
    query = property_query(today)
    if term := search.strip():
        query = query.where(or_(*(field.icontains(term, autoescape=True) for field in (
            Property.name, Property.address, Property.cadastral_number,
        ))))
    if type is not None:
        query = query.where(Property.type == type)
    if status is not None:
        query = query.where(property_status(today) == status)
    if exclude_archived:
        query = query.where(Property.status != PropertyStatus.ARCHIVED)
    if permitted := RENTAL_OBJECT_TYPES.get(contract_type):
        query = query.where(Property.type.in_(permitted))
    return paginate(db, query.order_by(Property.id.desc()), page, page_size, lambda row: property_view(*row))


@router.post("/properties", response_model=PropertyOut, status_code=201)
def create_property(data: PropertyIn, db: Db):
    reject_manual_usage(data.status)
    record = Property(**data.model_dump())
    db.add(record)
    commit(db, "Объект не удалось сохранить из-за конфликта данных.")
    return load_property(db, record.id, local_today())


def reject_manual_usage(status):
    if status == PropertyStatus.IN_USE:
        raise HTTPException(422, "Статус использования определяется автоматически по действующим договорам аренды.")


@router.get("/properties/{id}", response_model=PropertyOut)
def property_detail(id: Id, db: Db):
    return load_property(db, id, local_today())


@router.patch("/properties/{id}", response_model=PropertyOut)
def update_property(id: Id, data: PropertyPatch, db: Db):
    record = require(db, Property, id, "Объект не найден.", lock=True)
    today = local_today()
    reject_manual_usage(data.status)
    values = merge(PropertyIn, record, data)
    if values["status"] == PropertyStatus.ARCHIVED and record.status != PropertyStatus.ARCHIVED:
        if db.scalar(select(property_contracts(today)).select_from(Property).where(Property.id == id)):
            raise HTTPException(409, "Нельзя архивировать объект с действующим или ожидающим начала договором.")
    if values["type"] != record.type:
        rental_types = db.scalars(select(Contract.type).where(
            Contract.property_id == id, Contract.status == ContractStatus.ACTIVE,
            Contract.type.in_(tuple(RENTAL_OBJECT_TYPES)),
        )).all()
        if any(not compatible(kind, values["type"]) for kind in rental_types):
            raise HTTPException(409, "Новая категория объекта несовместима с одним из незавершённых договоров аренды.")
    if values["status"] != PropertyStatus.ARCHIVED:
        values["status"] = PropertyStatus.AVAILABLE
    for key, value in values.items():
        setattr(record, key, value)
    commit(db, "Объект не удалось сохранить из-за конфликта данных.")
    return load_property(db, id, today)


@router.delete("/properties/{id}", status_code=204)
def delete_property(id: Id, db: Db):
    db.delete(require(db, Property, id, "Объект не найден.", lock=True))
    commit(db, "Объект со связанными договорами можно только архивировать, когда договоры прекратят действие.")
    return Response(status_code=204)


@router.get("/counterparties", response_model=Page[CounterpartyOut])
def counterparties(db: Db, search: Search = "", page: PageNumber = 1, page_size: PageSize = 20):
    query = select(Counterparty)
    if term := search.strip():
        query = query.where(or_(
            Counterparty.name.icontains(term, autoescape=True),
            Counterparty.tax_id.icontains(term, autoescape=True),
        ))
    return paginate(db, query.order_by(Counterparty.name, Counterparty.id), page, page_size, lambda row: row[0])


@router.post("/counterparties", response_model=CounterpartyOut, status_code=201)
def create_counterparty(data: CounterpartyIn, db: Db):
    record = Counterparty(**data.model_dump())
    db.add(record)
    commit(db, "Контрагента не удалось сохранить из-за конфликта данных.")
    return record


@router.get("/counterparties/{id}", response_model=CounterpartyOut)
def counterparty_detail(id: Id, db: Db):
    return require(db, Counterparty, id, "Контрагент не найден.")


def contract_query(today):
    return (
        select(Contract, Property.name, Counterparty.name, contract_state(today), Property.status)
        .join(Property, Contract.property_id == Property.id)
        .join(Counterparty, Contract.counterparty_id == Counterparty.id)
    )


def contract_view(record, property_name, counterparty_name, state, property_status, today):
    values = ContractIn.model_validate(record).model_dump()
    return ContractOut(
        **values, id=record.id, status=record.status, property_name=property_name, counterparty_name=counterparty_name,
        days_remaining=(record.end_date - today).days if record.end_date else None,
        state=state, property_archived=property_status == PropertyStatus.ARCHIVED,
    )


@router.get("/contracts", response_model=Page[ContractOut])
def contracts(
    db: Db, search: Search = "", type: ContractType | None = None, status: ContractStatus | None = None,
    state: ContractState | None = None,
    property_id: Id | None = None, page: PageNumber = 1, page_size: PageSize = 20,
):
    today = local_today()
    query = contract_query(today)
    if term := search.strip():
        query = query.where(or_(*(field.icontains(term, autoescape=True) for field in (
            Contract.number, Property.name, Counterparty.name,
        ))))
    if type is not None:
        query = query.where(Contract.type == type)
    if status is not None:
        query = query.where(Contract.status == status)
    if state is not None:
        query = query.where(contract_state(today) == state)
    if property_id is not None:
        query = query.where(Contract.property_id == property_id)
    return paginate(db, query.order_by(Contract.id.desc()), page, page_size, lambda row: contract_view(*row, today))


@router.get("/contracts/{id}", response_model=ContractOut)
def contract_detail(id: Id, db: Db):
    return load_contract(db, id, local_today())


def load_contract(db, id, today):
    row = db.execute(contract_query(today).where(Contract.id == id)).one_or_none()
    if row is None:
        raise HTTPException(404, "Договор не найден.")
    return contract_view(*row, today)


def proposed_contract_state(db, status, values, today):
    return db.scalar(select(contract_state(
        today, literal(status), literal(values["start_date"], Date), literal(values["end_date"], Date),
    )))


def validate_contract_assignment(db, record, values, today):
    # Serialize contract assignment and scheduling with changes to the target property.
    prop = require(db, Property, values["property_id"], "Связанный объект не найден.", lock=True)
    changed_property = record.property_id != prop.id
    changed_schedule = any(getattr(record, key) != values[key] for key in ("start_date", "end_date"))
    incompatible = not compatible(values["type"], prop.type)
    scheduled = False
    if changed_schedule and (prop.status == PropertyStatus.ARCHIVED or incompatible):
        scheduled = proposed_contract_state(db, record.status, values, today) in OPEN_STATES
    if prop.status == PropertyStatus.ARCHIVED:
        if changed_property:
            raise HTTPException(409, "Нельзя назначить архивированный объект новому или перенесённому договору.")
        if scheduled:
            raise HTTPException(409, "Восстановите объект из архива перед активацией или переносом начала договора.")
    if incompatible and (changed_property or record.type != values["type"] or scheduled):
        raise HTTPException(422, "Аренда земли доступна для участков, аренда недвижимости — для зданий и нежилых помещений.")
    require(db, Counterparty, values["counterparty_id"], "Контрагент не найден.")


def prepare_contract(db, record, values, today):
    validate_contract_assignment(db, record, values, today)
    for key, value in values.items():
        setattr(record, key, value)
    db.add(record)


def persist_contract(db, record, today):
    commit(db, "Номер договора уже используется или связанная запись была удалена.")
    return load_contract(db, record.id, today)


def locked_contract(db, id, today):
    record = require(db, Contract, id, "Договор не найден.", lock=True)
    state = db.scalar(select(contract_state(today)).where(Contract.id == id))
    return record, state


@router.post("/contracts", response_model=ContractOut, status_code=201)
def create_contract(data: ContractIn, db: Db):
    today = local_today()
    record = Contract(status=ContractStatus.ACTIVE)
    prepare_contract(db, record, data.model_dump(), today)
    return persist_contract(db, record, today)


@router.patch("/contracts/{id}", response_model=ContractOut)
def update_contract(id: Id, data: ContractPatch, db: Db):
    today = local_today()
    record, state = locked_contract(db, id, today)
    if state not in OPEN_STATES and state != ContractState.EXPIRED:
        raise HTTPException(409, "Завершённый или расторгнутый договор нельзя редактировать.")

    values = merge(ContractIn, record, data)
    if state == ContractState.EXPIRED:
        if data.model_fields_set - {"note"}:
            raise HTTPException(409, "В договоре с истёкшим сроком можно изменить только примечание.")
        record.note = values["note"]
    else:
        if state == ContractState.ACTIVE:
            end = values["end_date"]
            if record.end_date is not None and (end is None or end > record.end_date):
                raise HTTPException(409, "Увеличить срок действующей аренды можно через действие «Продлить».")
            if proposed_contract_state(db, record.status, values, today) != ContractState.ACTIVE:
                raise HTTPException(409, "Изменённые даты должны оставлять договор действующим на сегодня.")
        prepare_contract(db, record, values, today)
    return persist_contract(db, record, today)


@router.post("/contracts/{id}/complete", response_model=ContractOut)
def complete_contract(id: Id, db: Db):
    today = local_today()
    record, state = locked_contract(db, id, today)
    if state not in (ContractState.ACTIVE, ContractState.EXPIRED):
        raise HTTPException(409, "Завершить можно действующий договор или договор с истёкшим сроком.")
    record.status = ContractStatus.COMPLETED
    return persist_contract(db, record, today)


@router.post("/contracts/{id}/terminate", response_model=ContractOut)
def terminate_contract(id: Id, db: Db):
    today = local_today()
    record, state = locked_contract(db, id, today)
    if state != ContractState.ACTIVE:
        raise HTTPException(409, "Расторгнуть можно только действующий договор.")
    record.status = ContractStatus.TERMINATED
    return persist_contract(db, record, today)


@router.post("/contracts/{id}/extend", response_model=ContractOut)
def extend_contract(id: Id, data: ContractExtension, db: Db):
    today = local_today()
    record, state = locked_contract(db, id, today)
    if state != ContractState.ACTIVE or record.type not in RENTAL_OBJECT_TYPES or record.end_date is None:
        raise HTTPException(409, "Продлить можно только действующий договор аренды с датой окончания.")
    if data.end_date <= record.end_date:
        raise HTTPException(422, "Новая дата окончания должна быть позже текущей даты окончания договора.")
    values = ContractIn.model_validate(record).model_dump()
    validate_contract_assignment(db, record, {**values, "end_date": data.end_date}, today)
    record.end_date = data.end_date
    return persist_contract(db, record, today)


@router.post("/contracts/{id}/successor", response_model=ContractOut, status_code=201)
def create_successor(id: Id, data: ContractIn, db: Db):
    today = local_today()
    source, state = locked_contract(db, id, today)
    if state in OPEN_STATES:
        raise HTTPException(409, "Создать новый договор на основе можно после истечения срока, завершения или расторжения.")
    if state == ContractState.EXPIRED and data.start_date <= source.end_date:
        raise HTTPException(422, "Новый договор должен начинаться после даты окончания предыдущего.")

    reference = f"На основе договора № {source.number}."
    note = data.note or ""
    if reference not in note:
        note = f"{reference}\n{note}" if note else reference
    if len(note) > 3000:
        raise HTTPException(422, "Примечание вместе со ссылкой на предыдущий договор должно содержать не более 3000 символов.")

    record = Contract(status=ContractStatus.ACTIVE)
    prepare_contract(db, record, {**data.model_dump(), "note": note}, today)
    if state == ContractState.EXPIRED:
        source.status = ContractStatus.COMPLETED
    return persist_contract(db, record, today)


@router.delete("/contracts/{id}", status_code=204)
def delete_contract(id: Id, db: Db):
    db.delete(require(db, Contract, id, "Договор не найден.", lock=True))
    commit(db, "У договора есть начисления. Удаление недоступно.")
    return Response(status_code=204)
