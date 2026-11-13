"""REST API surface."""

from __future__ import annotations

import csv
import io

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import PlainTextResponse, Response

from app.agents.schema_agent import get_catalog
from app.agents.sql_generator import is_commerce_dataset, suggested_questions
from app.api.schemas import ActivateRequest, AskRequest, ConnectionRequest, ValidateRequest
from app.config import get_settings
from app.database.connection import healthcheck
from app.graph import pipeline
from app.services import datasource, history_store
from app.services.datasource import DataSourceError, DataSourceNotFound
from app.services.llm_client import get_llm_client
from app.services.sql_validator import validate_sql

router = APIRouter(prefix="/api/v1", tags=["analytics"])

SUGGESTED_QUESTIONS = [
    "What was our total revenue last month?",
    "Show the top 5 customers by revenue.",
    "Which product category has the highest sales?",
    "Compare monthly revenue across 2025 and 2026.",
    "Find customers who have not placed an order in the last 90 days.",
    "What is the revenue share of each city?",
    "Which products generate the most revenue?",
]


@router.post("/ask")
def ask(payload: AskRequest) -> dict:
    return pipeline.ask(payload.question, payload.conversation_id)


@router.get("/health")
def health() -> dict:
    settings = get_settings()
    return {
        "database": healthcheck(settings),
        "datasource": datasource.active_summary(),
        "llm": {
            "provider": settings.llm_provider,
            "model": settings.ollama_model,
            **get_llm_client().health(),
        },
        "limits": {"max_rows": settings.max_rows, "query_timeout_seconds": settings.query_timeout_seconds},
        "allowed_tables": settings.allowed_tables,
    }


@router.get("/datasources")
def datasources() -> dict:
    """Registered sources plus which one the agent is currently pointed at."""
    try:
        return datasource.list_sources()
    except DataSourceError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/datasources/upload")
def upload_datasource(files: list[UploadFile] = File(...)) -> dict:
    """Ingest a SQLite file, a .sql dump, or a set of CSV/Excel tables."""
    try:
        record = datasource.add_uploads([(file.filename or "upload", file.file) for file in files])
    except DataSourceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"created": record, "active": datasource.active_summary()}


@router.post("/datasources/connection")
def add_connection(payload: ConnectionRequest) -> dict:
    try:
        record = datasource.add_connection(payload.name, payload.url)
    except DataSourceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"created": record, "active": datasource.active_summary()}


@router.put("/datasources/active")
def set_active_datasource(payload: ActivateRequest) -> dict:
    try:
        return datasource.activate(payload.id)
    except DataSourceNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DataSourceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/datasources/{source_id}")
def delete_datasource(source_id: str) -> dict:
    try:
        return datasource.remove(source_id)
    except DataSourceNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DataSourceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/schema")
def schema(refresh: bool = Query(default=False)) -> list[dict]:
    return get_catalog(refresh=refresh).as_api_payload()


@router.get("/suggestions")
def suggestions() -> list[str]:
    """Starter questions that the active schema can actually answer."""
    settings = get_settings()
    if is_commerce_dataset(settings):
        return SUGGESTED_QUESTIONS
    try:
        return suggested_questions(get_catalog(settings))
    except Exception:  # noqa: BLE001 - an unreadable source should not break the page
        return []


@router.post("/sql/validate", response_class=PlainTextResponse)
def check_sql(payload: ValidateRequest) -> str:
    """Dry-run hand-written SQL through the validator (used by the query editor)."""
    report = validate_sql(payload.sql)
    if report.ok:
        return "SAFE — this query may be executed.\n\n" + report.sql
    raise HTTPException(status_code=422, detail="; ".join(report.errors))


@router.get("/history")
def history(
    conversation_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict]:
    return [
        {key: value for key, value in run.items() if key != "result"}
        for run in history_store.list_runs(limit=limit, conversation_id=conversation_id)
    ]


@router.get("/history/{run_id}")
def history_detail(run_id: int) -> dict:
    run = history_store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Unknown run id.")
    return run


@router.delete("/history")
def clear_history(conversation_id: str | None = Query(default=None)) -> dict:
    return {"deleted": history_store.clear(conversation_id)}


@router.get("/export/{run_id}")
def export(run_id: int, fmt: str = Query(default="csv", pattern="^(csv|xlsx)$")) -> Response:
    run = history_store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Unknown run id.")
    result = run.get("result") or {}
    columns, rows = result.get("columns", []), result.get("rows", [])
    if not columns:
        raise HTTPException(status_code=409, detail="This run has no result set to export.")

    if fmt == "csv":
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer)
        writer.writerow(columns)
        writer.writerows(rows)
        return PlainTextResponse(
            buffer.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="result-{run_id}.csv"'},
        )

    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "result"
    sheet.append(columns)
    for row in rows:
        sheet.append(row)
    stream = io.BytesIO()
    workbook.save(stream)
    return Response(
        content=stream.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="result-{run_id}.xlsx"'},
    )
