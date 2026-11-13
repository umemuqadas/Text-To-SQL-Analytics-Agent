"""Database engines and sessions.

Two engines on purpose:

* ``admin_engine`` — used for schema introspection (and the demo seeder).
* ``reader_engine`` — the only engine that runs LLM-generated SQL. It is
  configured for autocommit, a server-side statement timeout (PostgreSQL) and,
  in production, a SELECT-only role.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

from app.config import Settings, get_settings

_engines: dict[str, Engine] = {}

_SQLITE_PREFIX = "sqlite:///"


def _as_readonly_sqlite(url: str) -> str:
    """Rewrite a plain SQLite path as a read-only pysqlite URI.

    Uploaded files are just files on disk, so writes must be refused by the
    driver as well as by the validator. Anything already carrying a ``mode``
    (an upload registered by ``app.services.datasource``) or an in-memory
    database is left alone.
    """
    if not url.startswith(_SQLITE_PREFIX) or ":memory:" in url or "mode=" in url:
        return url
    path = url.removeprefix(_SQLITE_PREFIX).split("?")[0]
    resolved = Path(path).resolve()
    if not resolved.is_file():
        return url
    return f"{_SQLITE_PREFIX}file:{resolved.as_posix()}?mode=ro&nolookup=1&uri=true"


def _build_engine(url: str, settings: Settings) -> Engine:
    if url.startswith("sqlite"):
        url = _as_readonly_sqlite(url)
    kwargs: dict = {"pool_pre_ping": True, "future": True}
    if url.startswith("sqlite"):
        # Enough concurrency for a single-process demo without file locking churn.
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": settings.query_timeout_seconds}
        if ":memory:" in url:
            kwargs["poolclass"] = StaticPool
    engine = create_engine(url, **kwargs)
    _attach_timeout_guard(engine, settings.query_timeout_seconds)
    return engine


def _attach_timeout_guard(engine: Engine, timeout_seconds: int) -> None:
    if engine.dialect.name != "postgresql":
        return

    @event.listens_for(engine, "connect")
    def _set_guardrails(dbapi_connection, _record) -> None:  # pragma: no cover - driver hook
        cursor = dbapi_connection.cursor()
        cursor.execute(f"SET statement_timeout = {int(timeout_seconds * 1000)}")
        cursor.execute("SET default_transaction_read_only = on")
        cursor.close()


def get_engine(kind: str = "admin", settings: Settings | None = None) -> Engine:
    settings = settings or get_settings()
    url = settings.database_url if kind == "admin" else settings.effective_reader_url
    key = f"{kind}:{url}"
    if key not in _engines:
        _engines[key] = _build_engine(url, settings)
    return _engines[key]


@contextmanager
def reader_session(settings: Settings | None = None) -> Iterator[object]:
    engine = get_engine("reader", settings)
    with engine.connect() as connection:
        yield connection


def healthcheck(settings: Settings | None = None) -> dict[str, str]:
    settings = settings or get_settings()
    status: dict[str, str] = {}
    for kind, url in (("database", settings.database_url), ("reader", settings.effective_reader_url)):
        try:
            engine = get_engine("admin" if kind == "database" else "reader", settings)
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            status[kind] = "ok"
        except Exception as exc:  # noqa: BLE001 - surface the reason to the UI
            status[kind] = f"error: {exc}"
    status["dialect"] = get_engine("admin", settings).dialect.name
    return status


def dispose_all() -> None:
    for engine in _engines.values():
        engine.dispose()
    _engines.clear()
