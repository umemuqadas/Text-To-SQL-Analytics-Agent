"""FastAPI application entrypoint.

Run with:  uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config import get_settings
from app.database.connection import dispose_all, healthcheck
from app.services import datasource


@asynccontextmanager
async def lifespan(_app: FastAPI):
    restored = datasource.resume_active()
    if restored:
        print(f"[text-to-sql] restored datasource: {restored['name']} ({restored['id']})")
    status = healthcheck()
    print(f"[text-to-sql] database: {status}")
    yield
    dispose_all()


settings = get_settings()
app = FastAPI(
    title="Text-to-SQL Analytics Agent",
    description="Ask questions in English, get validated SQL, results, charts and an AI summary.",
    version="1.0.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)


@app.get("/")
def root() -> dict:
    return {"service": "text-to-sql-analytics-agent", "docs": "/docs"}
