from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import OperationalError, ProgrammingError

from .config import ROOT
from .routers import dashboard, finance, registry
from .validation import FieldError, validation_errors

FRONTEND = ROOT / "frontend"
app = FastAPI(title="Учёт муниципального имущества", version="1.0.0")
app.include_router(dashboard.router, prefix="/api")
app.include_router(registry.router, prefix="/api")
app.include_router(finance.router, prefix="/api")
app.mount("/static", StaticFiles(directory=FRONTEND, check_dir=False), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(FRONTEND / "index.html")


@app.exception_handler(RequestValidationError)
async def invalid_request(_request: Request, exc: RequestValidationError):
    return JSONResponse({"detail": validation_errors(exc.errors())}, status_code=422)


@app.exception_handler(FieldError)
async def invalid_field(_request: Request, exc: FieldError):
    return JSONResponse({"detail": exc.detail, "errors": exc.errors}, status_code=exc.status_code)


@app.exception_handler(OperationalError)
async def database_unavailable(_request: Request, _exc: OperationalError):
    return JSONResponse({"detail": "PostgreSQL недоступна. Проверьте службу и настройки подключения."}, status_code=503)


@app.exception_handler(ProgrammingError)
async def database_programming_error(_request: Request, exc: ProgrammingError):
    if exc.orig.sqlstate == "42P01":
        return JSONResponse({"detail": "Таблицы не созданы. Выполните python -m backend.init_db."}, status_code=503)
    raise exc
