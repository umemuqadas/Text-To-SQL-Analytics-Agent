"""Uploadable databases: ingestion, registration and activation.

The agent can be repointed at another dataset while it runs — an uploaded
SQLite file, a set of CSV/Excel tables, a SQL dump replayed into a local file,
or a connection string. Whatever the source, its own tables become the
allow-list, so the validator's first rule ("only approved tables") still holds
on a schema nobody predicted, and every file-based source is attached read-only
at the driver level as well as by the validator.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import unquote, urlsplit

import sqlglot
from sqlglot import exp
from sqlalchemy import create_engine, inspect, text

from app.agents import schema_agent
from app.config import Settings, base_settings, get_settings, set_active_settings
from app.database import connection

DEMO_ID = "demo"

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
REGISTRY_PATH = DATA_DIR / "datasources.json"

MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_FILES = 40
MAX_TABLES = 100
CHUNK_STATEMENTS = 100

SQLITE_SUFFIXES = {".db", ".sqlite", ".sqlite3"}
TABLE_SUFFIXES = {".csv", ".tsv", ".txt", ".xlsx", ".xlsm"}
DUMP_SUFFIXES = {".sql"}
ALLOWED_SUFFIXES = SQLITE_SUFFIXES | TABLE_SUFFIXES | DUMP_SUFFIXES
ALLOWED_URL_SCHEMES = {"postgresql", "postgres", "mysql", "mariadb"}


class DataSourceError(ValueError):
    """Raised for anything the user can fix: bad file, bad URL, empty schema."""


class DataSourceNotFound(DataSourceError):
    """The requested id is not in the registry."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _display(value: str) -> str:
    """A filename for humans, with the directory part and spaces normalised."""
    return Path(unquote(value or "")).name


# ---------------------------------------------------------------------- registry
def _read_registry() -> dict:
    if not REGISTRY_PATH.is_file():
        return {"active_id": DEMO_ID, "sources": []}
    try:
        payload = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise DataSourceError(f"The datasource registry could not be read ({exc}).") from exc
    payload.setdefault("active_id", DEMO_ID)
    payload.setdefault("sources", [])
    return payload


def _write_registry(payload: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = REGISTRY_PATH.with_name("datasources.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(REGISTRY_PATH)


def _register(record: dict) -> None:
    payload = _read_registry()
    payload["sources"].append(record)
    _write_registry(payload)


def _find(payload: dict, source_id: str) -> dict:
    record = next((item for item in payload["sources"] if item["id"] == source_id), None)
    if record is None:
        raise DataSourceNotFound(f"No datasource with id {source_id}.")
    return record


def _new_id() -> str:
    return f"ds_{uuid.uuid4().hex[:12]}"


# ------------------------------------------------------------------------- names
def _safe_label(value: str, fallback: str) -> str:
    cleaned = re.sub(r"\s+", " ", (value or "").strip())
    cleaned = re.sub(r'[\\/:*?"<>|]', "_", cleaned).strip(" .")[:60]
    return cleaned or fallback


def _stage_name(value: str, taken: set[str]) -> str:
    """A filename inside the staging directory that cannot escape it."""
    base = _display(value)
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", base)[:70].strip("._") or "file"
    candidate, index = stem.lower(), 1
    while candidate in taken:
        index += 1
        parts = Path(stem)
        candidate = f"{parts.stem}_{index}{parts.suffix}".lower()
    return candidate


def _identifier(value: str, fallback: str) -> str:
    """A SQL-safe, unquoted table or column name."""
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", str(value or "").strip()).strip("_").lower()
    if not cleaned:
        return fallback
    if cleaned[0].isdigit():
        cleaned = f"t_{cleaned}"
    return cleaned[:50]


def _unique(name: str, used: set[str]) -> str:
    candidate, index = name, 1
    while candidate in used:
        index += 1
        candidate = f"{name}_{index}"
    used.add(candidate)
    return candidate


def _group(suffix: str) -> str:
    if suffix in SQLITE_SUFFIXES:
        return "sqlite"
    if suffix in DUMP_SUFFIXES:
        return "dump"
    return "table"


# ------------------------------------------------------------------------- query
def discover_tables(url: str) -> list[str]:
    engine = _engine_for(url)
    try:
        inspector = inspect(engine)
        return sorted(set(inspector.get_table_names()) | set(inspector.get_view_names()))
    except Exception as exc:  # noqa: BLE001 - the driver text is the useful message
        raise DataSourceError(f"Could not read the schema: {_reason(exc)}") from exc
    finally:
        engine.dispose()


def _probe(url: str) -> None:
    engine = _engine_for(url)
    try:
        with engine.connect() as connection_:
            connection_.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        raise DataSourceError(f"Could not connect: {_reason(exc)}") from exc
    finally:
        engine.dispose()


def _engine_for(url: str):
    kwargs = {"future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(url, **kwargs)


def _reason(exc: Exception) -> str:
    return str(getattr(exc, "orig", exc)).strip()[:200] or exc.__class__.__name__


def _readonly_sqlite_url(path: Path) -> str:
    return f"sqlite:///file:{path.resolve().as_posix()}?mode=ro&nolookup=1&uri=true"


def _human_size(value: int) -> str:
    for unit, factor in (("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)):
        if value >= factor:
            return f"{value // factor} {unit}"
    return f"{value} bytes"


# ---------------------------------------------------------------------- uploads
def _stream_to(target: Path, stream: BinaryIO, budget_left: int) -> int:
    target.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with target.open("wb") as handle:
        while chunk := stream.read(512 * 1024):
            written += len(chunk)
            if written > budget_left:
                raise DataSourceError(
                    f"That upload is larger than the {_human_size(budget_left)} limit."
                )
            handle.write(chunk)
    return written


def add_uploads(files: list[tuple[str, BinaryIO]]) -> dict:
    """Create one source from a set of uploads.

    A single SQLite file attaches as-is, a single ``.sql`` dump is replayed into
    a new local file, and any number of CSV/Excel files build one dataset with a
    table per file (or per sheet). Mixing those kinds is rejected rather than
    guessed at.
    """
    if not files:
        raise DataSourceError("No files were received.")
    if len(files) > MAX_FILES:
        raise DataSourceError(f"Upload at most {MAX_FILES} files at a time.")

    names = [name for name, _ in files]
    suffixes = [Path(_display(name)).suffix.lower() for name in names]
    unknown = sorted({s for s in suffixes if s not in ALLOWED_SUFFIXES})
    if unknown:
        raise DataSourceError(
            f"Unsupported file type(s) {', '.join(unknown)}. "
            "Upload a SQLite file, a .sql dump, or CSV/Excel tables."
        )
    groups = {_group(s) for s in suffixes}
    if len(groups) > 1:
        raise DataSourceError("Upload one SQLite file, one .sql dump, or a set of CSV/Excel tables — not a mix.")
    if len(files) > 1 and groups != {"table"}:
        raise DataSourceError("A SQLite file or a .sql dump has to be uploaded on its own.")

    source_id = _new_id()
    directory = UPLOAD_DIR / source_id
    taken: set[str] = set()
    staged: list[tuple[str, Path]] = []
    total = 0
    try:
        for name, stream in files:
            target = directory / _stage_name(name, taken)
            taken.add(target.name)
            total += _stream_to(target, stream, MAX_UPLOAD_BYTES - total)
            staged.append((name, target))

        kind = groups.pop()
        if kind == "sqlite":
            record = _ingest_sqlite(source_id, names[0], staged[0][1])
        elif kind == "dump":
            record = _ingest_dump(source_id, names[0], staged[0][1])
        else:
            record = _ingest_tables(source_id, names, staged)
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise

    record["size_bytes"] = total
    _register(record)
    return _public(record)


def _finish(record: dict, directory: Path | None) -> dict:
    """Read the schema back so the allow-list is the source's own, not a guess."""
    tables = discover_tables(record["url"])
    if not tables:
        raise DataSourceError("No tables were found in that source, so there is nothing to query.")
    record["tables"] = tables[:MAX_TABLES]
    if len(tables) > MAX_TABLES:
        record["notes"].append(
            f"{len(tables)} tables exist; only the first {MAX_TABLES} are exposed to the agent."
        )
    if directory is not None:
        record["directory"] = str(directory)
    return record


def _ingest_sqlite(source_id: str, original_name: str, path: Path) -> dict:
    url = _readonly_sqlite_url(path)
    _probe(url)
    record = {
        "id": source_id,
        "name": _safe_label(Path(_display(original_name)).stem, "Uploaded database"),
        "kind": "sqlite",
        "origin": _display(original_name),
        "created_at": _now(),
        "url": url,
        "notes": ["Attached read-only — the uploaded file is never modified."],
    }
    return _finish(record, path.parent)


def _ingest_dump(source_id: str, original_name: str, path: Path) -> dict:
    """Replay CREATE TABLE / INSERT statements from a dump into a new SQLite file."""
    statements = _parse_dump(path.read_text(encoding="utf-8", errors="replace"))
    target = path.parent / "dataset.db"
    created = skipped = failed = 0
    failures: list[str] = []

    raw = sqlite3.connect(target)
    try:
        for index, statement in enumerate(statements, start=1):
            if statement is None:
                continue
            is_table_ddl = isinstance(statement, exp.Create) and statement.kind == "TABLE"
            if not (is_table_ddl or isinstance(statement, exp.Insert)):
                skipped += 1
                continue
            try:
                raw.executescript(statement.sql(dialect="sqlite") + ";")
                created += 1
            except sqlite3.Error as exc:  # noqa: PERF203 - vendor SQL often needs a tweak
                failed += 1
                if len(failures) < 3:
                    failures.append(str(exc)[:160])
            if index % CHUNK_STATEMENTS == 0:
                raw.commit()
        raw.commit()
    finally:
        raw.close()

    notes = [f"Replayed {created} statement(s) into a local SQLite file."]
    if skipped:
        notes.append(
            f"Skipped {skipped} statement(s) that were not CREATE TABLE or INSERT. "
            "pg_dump's COPY format is not supported — export INSERT statements, or upload CSV instead."
        )
    if failed:
        notes.append(f"{failed} statement(s) were rejected by SQLite: {' | '.join(failures)}")
    if created == 0:
        raise DataSourceError(f"Nothing could be replayed from that dump. {notes[-1]}")

    url = _readonly_sqlite_url(target)
    record = {
        "id": source_id,
        "name": _safe_label(Path(_display(original_name)).stem, "Imported dump"),
        "kind": "dump",
        "origin": _display(original_name),
        "created_at": _now(),
        "url": url,
        "notes": notes,
    }
    return _finish(record, target.parent)


def _parse_dump(script: str) -> list[exp.Expression | None]:
    if not script.strip():
        raise DataSourceError("That .sql file is empty.")
    for dialect in (None, "mysql", "postgres"):
        try:
            return [statement for statement in sqlglot.parse(script, read=dialect)]
        except sqlglot.errors.ParseError:
            continue
    raise DataSourceError(
        "That .sql file could not be parsed. Export plain CREATE TABLE and INSERT statements, "
        "or upload the data as CSV instead."
    )


def _ingest_tables(source_id: str, names: list[str], staged: list[tuple[str, Path]]) -> dict:
    """Build one SQLite dataset from CSV/Excel uploads, a table per file or sheet."""
    import pandas as pd

    target = staged[0][1].parent / "dataset.db"
    raw = sqlite3.connect(target)
    tables: list[str] = []
    notes: list[str] = []
    used: set[str] = set()
    try:
        for original, path in staged:
            frames = _read_frames(pd, path)
            stem = _identifier(Path(_display(original)).stem, f"table{len(tables) + 1}")
            for sheet, frame in frames.items():
                if frame.empty:
                    notes.append(f"{_display(original)}: sheet '{sheet}' was empty and was skipped.")
                    continue
                table = _unique(f"{stem}_{_identifier(sheet, 'rows')}" if len(frames) > 1 else stem, used)
                _clean_frame(frame).to_sql(table, raw, if_exists="replace", index=False)
                tables.append(table)
        raw.commit()
    finally:
        raw.close()

    if not tables:
        raise DataSourceError("None of those files contained usable rows.")

    label = _safe_label(Path(_display(names[0])).stem, "Uploaded tables")
    if len(names) > 1:
        label = f"{label} + {len(names) - 1} more"
    notes.insert(0, f"Built a local SQLite dataset with {len(tables)} table(s) from {len(names)} file(s).")
    url = _readonly_sqlite_url(target)
    record = {
        "id": source_id,
        "name": label,
        "kind": "tables",
        "origin": ", ".join(_display(name) for name in names[:5]),
        "created_at": _now(),
        "url": url,
        "notes": notes,
    }
    return _finish(record, target.parent)


def _read_frames(pd_module, path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xlsm"}:
        return {str(sheet): frame for sheet, frame in pd_module.read_excel(path, sheet_name=None).items()}
    separator = "\t" if suffix in {".tsv", ".txt"} else ","
    return {"data": pd_module.read_csv(path, sep=separator)}


def _clean_frame(frame):
    """Drop blank columns and rename headers so they need no quoting in SQL."""
    frame = frame.dropna(axis=1, how="all")
    return frame.rename(columns=_safe_columns(frame.columns))


def _safe_columns(columns) -> dict:
    used: set[str] = set()
    return {column: _unique(_identifier(str(column), "column"), used) for column in columns}


# ------------------------------------------------------------------ connections
def add_connection(name: str, url: str) -> dict:
    """Register an external database from a connection string."""
    cleaned = (url or "").strip()
    parsed = urlsplit(cleaned)
    scheme = parsed.scheme.lower()
    if scheme == "sqlite":
        raise DataSourceError("A local file is uploaded rather than pasted — use the upload box for SQLite files.")
    if scheme not in ALLOWED_URL_SCHEMES:
        raise DataSourceError(
            f"'{parsed.scheme or 'that'}' is not a supported scheme. Use PostgreSQL or MySQL."
        )
    if not parsed.hostname:
        raise DataSourceError(
            "That connection string has no host. Example: postgresql://analyst@localhost:5432/shop"
        )

    driver_url = re.sub(r"^postgres(ql)?://", "postgresql+psycopg2://", cleaned, count=1)
    _probe(driver_url)

    port = f":{parsed.port}" if parsed.port else ""
    record = {
        "id": _new_id(),
        "name": _safe_label(name, "") or _safe_label(f"{parsed.hostname}{port}{parsed.path}", "Connection"),
        "kind": "connection",
        "origin": f"{scheme}://{parsed.hostname}{port}{parsed.path}",
        "created_at": _now(),
        # Stored so the source survives a restart, and never returned by the API.
        "url": driver_url,
        "notes": [
            "Queries still pass the read-only validator and the row cap.",
            "For defence-in-depth, create a SELECT-only role on the server and use it here.",
            "This connection string, password included, is stored in data/datasources.json.",
        ],
    }
    _finish(record, None)
    _register(record)
    return _public(record)


# ------------------------------------------------------------------- activation
def _demo_record() -> dict:
    settings = base_settings()
    return {
        "id": DEMO_ID,
        "name": settings.datasource_name,
        "kind": "builtin",
        "origin": "database/schema.sql + database/sample_data.sql",
        "created_at": None,
        "tables": list(settings.allowed_tables),
        "notes": [],
        "built_in": True,
    }


def _public(record: dict) -> dict:
    """Strips the stored DSN and filesystem paths — connection strings hold credentials."""
    return {key: value for key, value in record.items() if key not in {"url", "directory"}}


def list_sources() -> dict:
    payload = _read_registry()
    return {
        "active_id": payload["active_id"],
        "sources": [_demo_record()] + [_public(record) for record in payload["sources"]],
    }


def active_id() -> str:
    return _read_registry()["active_id"]


def active_summary() -> dict:
    settings = get_settings()
    return {
        "id": settings.datasource_id,
        "name": settings.datasource_name,
        "kind": settings.datasource_kind,
        "tables": len(settings.allowed_tables),
        "dialect": settings.database_url.split("://", 1)[0].split("+", 1)[0],
    }


def _settings_for(record: dict) -> Settings:
    base = base_settings()
    tables = discover_tables(record["url"])
    if not tables:
        raise DataSourceError(f"{record['name']} contains no readable tables.")
    return base.model_copy(
        update={
            "database_url": record["url"],
            "reader_database_url": record["url"],
            "allowed_tables": tables[:MAX_TABLES],
            # A protected column from another dataset must not shadow this schema.
            "hidden_columns": [
                column for column in base.hidden_columns if column.split(".", 1)[0] in set(tables)
            ],
            "datasource_id": record["id"],
            "datasource_name": record["name"],
            "datasource_kind": record["kind"],
        }
    )


def activate(source_id: str | None) -> dict:
    """Point the pipeline at a source; ``demo`` restores the sample warehouse."""
    payload = _read_registry()
    if source_id in (None, "", DEMO_ID):
        settings: Settings | None = None
        payload["active_id"] = DEMO_ID
    else:
        record = _find(payload, source_id)
        settings = _settings_for(record)
        payload["active_id"] = source_id

    set_active_settings(settings)
    connection.dispose_all()
    schema_agent.clear_cache()
    _write_registry(payload)
    return active_summary()


def resume_active() -> dict | None:
    """Re-apply the saved choice when the API process starts."""
    payload = _read_registry()
    if payload["active_id"] == DEMO_ID:
        return None
    record = next((item for item in payload["sources"] if item["id"] == payload["active_id"]), None)
    if record is None:
        activate(None)
        return None
    try:
        set_active_settings(_settings_for(record))
    except DataSourceError:
        # The file moved or the server is down: fall back instead of refusing to boot.
        payload["active_id"] = DEMO_ID
        _write_registry(payload)
        set_active_settings(None)
        return None
    schema_agent.clear_cache()
    return active_summary()


def remove(source_id: str) -> dict:
    if source_id == DEMO_ID:
        raise DataSourceError("The sample warehouse is built in and cannot be deleted.")
    payload = _read_registry()
    record = _find(payload, source_id)

    payload["sources"] = [item for item in payload["sources"] if item["id"] != source_id]
    was_active = payload["active_id"] == source_id
    if was_active:
        payload["active_id"] = DEMO_ID
    _write_registry(payload)

    directory = Path(record.get("directory") or "")
    if directory.is_dir() and UPLOAD_DIR in directory.parents:
        shutil.rmtree(directory, ignore_errors=True)
    if was_active:
        activate(None)
    return {"removed": source_id, "active": active_summary()}
