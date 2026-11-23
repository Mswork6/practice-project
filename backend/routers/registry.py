from fastapi import APIRouter, HTTPException, Response
from sqlalchemy import or_, select

from ..config import local_today
from ..database import Db, commit
from ..models import Contract, ContractStatus, ContractType, Counterparty, Property, PropertyStatus, PropertyType
from ..schemas import ContractIn, ContractOut, ContractPatch, CounterpartyIn, CounterpartyOut, Id
from ..schemas import Page, PropertyIn, PropertyOut, PropertyPatch
from .common import PageNumber, PageSize, Search, merge, paginate, require

router = APIRouter(tags=["Реестры"])


@router.get("/properties", response_model=Page[PropertyOut])
def properties(
    db: Db, search: Search = "", type: PropertyType | None = None, status: PropertyStatus | None = None,
    page: PageNumber = 1, page_size: PageSize = 20,
):
    query = select(Property)
    if term := search.strip():
        query = query.where(or_(*(field.icontains(term, autoescape=True) for field in (
            Property.name, Property.address, Property.cadastral_number,
        ))))
    if type is not None:
        query = query.where(Property.type == type)
    if status is not None:
        query = query.where(Property.status == status)
    return paginate(db, query.order_by(Property.id.desc()), page, page_size, lambda row: row[0])


@router.post("/properties", response_model=PropertyOut, status_code=201)
def create_property(data: PropertyIn, db: Db):
    record = Property(**data.model_dump())
    db.add(record)
    commit(db, "Объект не удалось сохранить из-за конфликта данных.")
    return record


@router.get("/properties/{id}", response_model=PropertyOut)
def property_detail(id: Id, db: Db):
    return require(db, Property, id, "Объект не найден.")


@router.patch("/properties/{id}", response_model=PropertyOut)
def update_property(id: Id, data: PropertyPatch, db: Db):
    record = property_detail(id, db)
    for key, value in merge(PropertyIn, record, data).items():
        setattr(record, key, value)
    commit(db, "Объект не удалось сохранить из-за конфликта данных.")
    return record


@router.delete("/properties/{id}", status_code=204)
def delete_property(id: Id, db: Db):
    db.delete(property_detail(id, db))
    commit(db, "У объекта есть договоры. Переведите объект в архив через редактирование.")
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


def contract_query():
    return (
        select(Contract, Property.name, Counterparty.name)
        .join(Property, Contract.property_id == Property.id)
        .join(Counterparty, Contract.counterparty_id == Counterparty.id)
    )


def contract_view(record, property_name, counterparty_name, today):
    values = ContractIn.model_validate(record).model_dump()
    return ContractOut(
        **values, id=record.id, property_name=property_name, counterparty_name=counterparty_name,
        days_remaining=(record.end_date - today).days if record.end_date else None,
    )


@router.get("/contracts", response_model=Page[ContractOut])
def contracts(
    db: Db, search: Search = "", type: ContractType | None = None, status: ContractStatus | None = None,
    property_id: Id | None = None, page: PageNumber = 1, page_size: PageSize = 20,
):
    query = contract_query()
    if term := search.strip():
        query = query.where(or_(*(field.icontains(term, autoescape=True) for field in (
            Contract.number, Property.name, Counterparty.name,
        ))))
    if type is not None:
        query = query.where(Contract.type == type)
    if status is not None:
        query = query.where(Contract.status == status)
    if property_id is not None:
        query = query.where(Contract.property_id == property_id)
    today = local_today()
    return paginate(db, query.order_by(Contract.id.desc()), page, page_size, lambda row: contract_view(*row, today))


@router.get("/contracts/{id}", response_model=ContractOut)
def contract_detail(id: Id, db: Db):
    row = db.execute(contract_query().where(Contract.id == id)).one_or_none()
    if row is None:
        raise HTTPException(404, "Договор не найден.")
    return contract_view(*row, local_today())


def save_contract(db, record, values):
    prop = require(db, Property, values["property_id"], "Связанный объект не найден.")
    party = require(db, Counterparty, values["counterparty_id"], "Контрагент не найден.")
    for key, value in values.items():
        setattr(record, key, value)
    db.add(record)
    commit(db, "Номер договора уже используется или связанная запись была удалена.")
    return contract_view(record, prop.name, party.name, local_today())


@router.post("/contracts", response_model=ContractOut, status_code=201)
def create_contract(data: ContractIn, db: Db):
    return save_contract(db, Contract(), data.model_dump())


@router.patch("/contracts/{id}", response_model=ContractOut)
def update_contract(id: Id, data: ContractPatch, db: Db):
    record = require(db, Contract, id, "Договор не найден.")
    return save_contract(db, record, merge(ContractIn, record, data))


@router.delete("/contracts/{id}", status_code=204)
def delete_contract(id: Id, db: Db):
    db.delete(require(db, Contract, id, "Договор не найден."))
    commit(db, "У договора есть начисления. Удаление недоступно.")
    return Response(status_code=204)
