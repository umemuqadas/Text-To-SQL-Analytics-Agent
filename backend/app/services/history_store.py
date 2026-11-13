"""Durable query history and conversation memory.

Kept in its own SQLite file so it works whatever the analytics database is,
and so the analytics reader role never needs write access.
"""

from __future__ import annotations

import datetime as dt
import json
from functools import lru_cache
from pathlib import Path

from sqlalchemy import (
    Column,
    DateTime,
    Engine,
    Integer,
    String,
    Text,
    create_engine,
    delete,
    insert,
    select,
)
from sqlalchemy.orm import declarative_base, Session

from app.config import Settings, get_settings

Base = declarative_base()


class QueryRun(Base):
    __tablename__ = "query_runs"

    id = Column(Integer, primary_key=True)
    conversation_id = Column(String(64), index=True, nullable=False, default="default")
    question = Column(Text, nullable=False)
    sql = Column(Text, nullable=False, default="")
    status = Column(String(32), nullable=False)  # executed | rejected | error
    summary = Column(Text, nullable=False, default="")
    error = Column(Text)
    row_count = Column(Integer, nullable=False, default=0)
    duration_ms = Column(Integer, nullable=False, default=0)
    provider = Column(String(64), nullable=False, default="")
    chart_type = Column(String(32), nullable=False, default="table")
    visualization_json = Column(Text, nullable=False, default="{}")
    result_json = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime, nullable=False, default=lambda: dt.datetime.now(dt.timezone.utc))


@lru_cache
def _engine_for(url: str) -> Engine:
    if url.startswith("sqlite"):
        path = url.split(":///")[-1].split("?")[0]
        if path and path != ":memory:":
            Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, future=True)
    Base.metadata.create_all(engine)
    return engine


def _session(settings: Settings) -> Session:
    return Session(_engine_for(settings.history_db_url))


def record_run(
    *,
    question: str,
    sql: str,
    status: str,
    summary: str,
    result_payload: dict,
    settings: Settings | None = None,
    conversation_id: str = "default",
    error: str | None = None,
    provider: str = "",
    chart_type: str = "table",
    visualization: dict | None = None,
) -> int:
    settings = settings or get_settings()
    with _session(settings) as session:
        run = QueryRun(
            conversation_id=conversation_id,
            question=question,
            sql=sql,
            status=status,
            summary=summary,
            error=error,
            row_count=result_payload.get("row_count", 0),
            duration_ms=result_payload.get("duration_ms", 0),
            provider=provider,
            chart_type=chart_type,
            visualization_json=json.dumps(visualization or {}),
            result_json=json.dumps(result_payload),
        )
        session.add(run)
        session.commit()
        return int(run.id)


def list_runs(limit: int = 50, conversation_id: str | None = None, settings: Settings | None = None) -> list[dict]:
    settings = settings or get_settings()
    with _session(settings) as session:
        stmt = select(QueryRun).order_by(QueryRun.created_at.desc()).limit(limit)
        if conversation_id:
            stmt = stmt.where(QueryRun.conversation_id == conversation_id)
        return [_serialize(run) for run in session.scalars(stmt)]


def get_run(run_id: int, settings: Settings | None = None) -> dict | None:
    settings = settings or get_settings()
    with _session(settings) as session:
        run = session.get(QueryRun, run_id)
        return _serialize(run) if run else None


def recent_turns(conversation_id: str, settings: Settings | None = None) -> list[dict]:
    """Last accepted question/SQL pairs, oldest first — the memory the SQL
    generator needs to resolve follow-ups such as "now only from Karachi"."""
    settings = settings or get_settings()
    with _session(settings) as session:
        stmt = (
            select(QueryRun.question, QueryRun.sql)
            .where(QueryRun.conversation_id == conversation_id, QueryRun.status == "executed")
            .order_by(QueryRun.created_at.desc())
            .limit(settings.conversation_window)
        )
        turns = [{"question": row[0], "sql": row[1]} for row in session.execute(stmt)]
    return list(reversed(turns))


def clear(conversation_id: str | None = None, settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    with _session(settings) as session:
        stmt = delete(QueryRun)
        if conversation_id:
            stmt = stmt.where(QueryRun.conversation_id == conversation_id)
        result = session.execute(stmt)
        session.commit()
        return int(result.rowcount or 0)


def _serialize(run: QueryRun) -> dict:
    return {
        "id": run.id,
        "conversation_id": run.conversation_id,
        "question": run.question,
        "sql": run.sql,
        "status": run.status,
        "summary": run.summary,
        "error": run.error,
        "row_count": run.row_count,
        "duration_ms": run.duration_ms,
        "provider": run.provider,
        "chart_type": run.chart_type,
        "visualization": json.loads(run.visualization_json or "{}"),
        "result": json.loads(run.result_json or "{}"),
        "created_at": run.created_at.isoformat(),
    }
