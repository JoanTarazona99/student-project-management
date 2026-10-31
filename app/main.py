from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.auth import router as auth_router
from app.materials import router as materials_router
from app.planning import router as planning_router
from app.projects import router as projects_router
from app.users import router as users_router
from app.database import engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    engine.dispose()


app = FastAPI(
    title="Управление учебными проектами студентов",
    description="Учебная ИС. Лабораторная работа № 5.",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(auth_router)
app.include_router(materials_router)
app.include_router(planning_router)
app.include_router(projects_router)
app.include_router(users_router)


@app.get("/", tags=["Система"])
def root():
    return {
        "message": "Сервер ИС управления учебными проектами запущен.",
        "documentation": "/docs",
        "database_check": "/health",
    }


@app.get("/health", tags=["Система"])
def health():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1")).scalar_one()
        return {
            "status": "ok",
            "database": "ok",
            "message": "Подключение к PostgreSQL работает.",
        }
    except SQLAlchemyError:
        raise HTTPException(503, "База данных временно недоступна.") from None
