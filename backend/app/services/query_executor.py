"""Executes validated SQL against the read-only connection."""

from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import text

from app.config import Settings, get_settings
from app.database.connection import reader_session

_JSON_SCALARS = (str, int, float, bool, type(None))


@dataclass(slots=True)
class QueryResult:
    sql: str
    columns: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    duration_ms: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def to_dicts(self) -> list[dict]:
        return [dict(zip(self.columns, row)) for row in self.rows]

    def to_payload(self) -> dict:
        return {
            "sql": self.sql,
            "columns": self.columns,
            "rows": self.rows,
            "row_count": self.row_count,
            "truncated": self.truncated,
            "duration_ms": self.duration_ms,
            "error": self.error,
        }

    @classmethod
    def from_payload(cls, payload: dict, sql: str) -> "QueryResult":
        return cls(
            sql=sql,
            columns=payload.get("columns", []),
            rows=payload.get("rows", []),
            row_count=payload.get("row_count", 0),
            truncated=payload.get("truncated", False),
            duration_ms=payload.get("duration_ms", 0),
            error=payload.get("error"),
        )

    def to_dict(self) -> dict:
        return self.to_payload()


def _jsonable(value: object) -> object:
    if isinstance(value, _JSON_SCALARS):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", "replace")
    return str(value)


def execute_query(sql: str, settings: Settings | None = None) -> QueryResult:
    settings = settings or get_settings()
    cap = settings.max_rows
    started = time.perf_counter()
    try:
        with reader_session(settings) as connection:
            result = connection.execute(text(sql))
            columns = list(result.keys())
            fetched = result.fetchmany(cap + 1)
    except Exception as exc:  # noqa: BLE001 - the DB error text feeds the repair loop
        return QueryResult(
            sql=sql,
            error=str(getattr(exc, "orig", exc)).strip() or exc.__class__.__name__,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    rows = [[_jsonable(value) for value in row] for row in fetched[:cap]]
    return QueryResult(
        sql=sql,
        columns=columns,
        rows=rows,
        row_count=len(rows),
        truncated=len(fetched) > cap,
        duration_ms=int((time.perf_counter() - started) * 1000),
    )
