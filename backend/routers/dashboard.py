from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from ..config import local_today
from ..database import Db
from ..schemas import Dashboard, Period
from ..services.dashboard import calculate

router = APIRouter(tags=["Аналитика"])


@router.get("/dashboard", response_model=Dashboard)
def dashboard(db: Db, period: Period | None = None, year: Annotated[int | None, Query(ge=1, le=9999)] = None):
    today = local_today()
    if period in (None, "year") and year is not None and year > today.year:
        raise HTTPException(422, "Выберите текущий или прошедший год.")
    # KPI and charts must describe the same snapshot, even if a payment arrives mid-request.
    db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
    return calculate(db, period or "year", year, today, calendar_dashboard=period is None)
