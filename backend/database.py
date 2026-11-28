from collections.abc import Iterator
from functools import cache
from typing import Annotated

from fastapi import Depends, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import database_url


@cache
def get_engine():
    return create_engine(
        database_url(),
        pool_pre_ping=True,
        isolation_level="READ COMMITTED",
        connect_args={"connect_timeout": 5},
    )


def get_db() -> Iterator[Session]:
    with Session(get_engine(), expire_on_commit=False) as db:
        yield db


Db = Annotated[Session, Depends(get_db)]


def commit(db: Session, conflict: str, *, constraints=None) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        error = constraints and constraints.get(exc.orig.diag.constraint_name or exc.orig.diag.column_name)
        if error:
            raise HTTPException(*error) from None
        if exc.orig.sqlstate in {"23503", "23505"}:
            raise HTTPException(409, conflict) from None
        if exc.orig.sqlstate == "23514":
            raise HTTPException(422, "Проверьте значения полей и согласованность дат.") from None
        raise
