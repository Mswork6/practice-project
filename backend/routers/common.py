from typing import Annotated

from fastapi import HTTPException, Query
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..schemas import Page

PageNumber = Annotated[int, Query(ge=1, le=100_000)]
PageSize = Annotated[int, Query(ge=1, le=100)]
Search = Annotated[str, Query(max_length=200)]


def require(db: Session, model, id: int, message: str):
    row = db.get(model, id)
    if row is None:
        raise HTTPException(404, message)
    return row


def paginate(db, query, page: int, page_size: int, serialize):
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    rows = db.execute(query.limit(page_size).offset((page - 1) * page_size))
    return Page(items=[serialize(row) for row in rows], total=total, page=page, page_size=page_size)


def merge(model, record, patch):
    fields = patch.model_dump(exclude_unset=True)
    merged = {name: getattr(record, name) for name in model.model_fields}
    try:
        return model.model_validate(merged | fields).model_dump()
    except ValidationError as exc:
        message = exc.errors(include_input=False)[0]
        detail = str(message["ctx"]["error"]) if "ctx" in message else "Проверьте обязательные поля."
        raise HTTPException(422, detail) from None
