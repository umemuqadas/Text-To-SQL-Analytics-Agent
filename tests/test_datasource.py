"""Uploadable datasources: ingestion guards, activation and the generic generator."""

from __future__ import annotations

import io
import sqlite3

import pytest

from app.config import Settings, set_active_settings
from app.services import datasource
from app.services.datasource import DataSourceError
from app.services.query_executor import execute_query
from app.services.sql_validator import validate_sql

FILMS = [
    (1, "RRR", "Action", "Telugu", 2022, 145000000.0, 187),
    (2, "Lagaan", "Drama", "Hindi", 2001, 6500000.0, 224),
    (3, "3 Idiots", "Comedy", "Hindi", 2009, 46000000.0, 171),
]


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """Keep the registry, uploads and the active override out of the real data dir."""
    monkeypatch.setattr(datasource, "DATA_DIR", tmp_path)
    monkeypatch.setattr(datasource, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(datasource, "REGISTRY_PATH", tmp_path / "datasources.json")
    set_active_settings(None)
    yield tmp_path
    set_active_settings(None)


def _films_database(path) -> None:
    raw = sqlite3.connect(path)
    raw.executescript(
        "CREATE TABLE films (film_id INTEGER PRIMARY KEY, title TEXT, genre TEXT,"
        " language TEXT, release_year INTEGER, box_office REAL, runtime_min INTEGER);"
    )
    raw.executemany("INSERT INTO films VALUES (?,?,?,?,?,?,?)", FILMS)
    raw.commit()
    raw.close()


def _upload_sqlite(tmp_path) -> dict:
    source = tmp_path / "films.db"
    _films_database(source)
    with source.open("rb") as handle:
        return datasource.add_uploads([("films.db", handle)])


# -------------------------------------------------------------------- ingestion
def test_sqlite_upload_is_attached_read_only(registry):
    record = _upload_sqlite(registry)
    assert record["kind"] == "sqlite"
    assert record["tables"] == ["films"]
    assert "mode=ro" in datasource._read_registry()["sources"][0]["url"]


def test_upload_of_unsupported_type_is_rejected(registry):
    with pytest.raises(DataSourceError, match="Unsupported file type"):
        datasource.add_uploads([("notes.exe", io.BytesIO(b"\x00\x01"))])
    assert not (registry / "uploads").exists()


def test_upload_size_limit_is_enforced(registry, monkeypatch):
    monkeypatch.setattr(datasource, "MAX_UPLOAD_BYTES", 64)
    with pytest.raises(DataSourceError, match="larger than"):
        datasource.add_uploads([("big.db", io.BytesIO(b"x" * 512))])
    assert datasource.list_sources()["sources"] == [datasource._demo_record()]


def test_mixed_upload_kinds_are_rejected(registry):
    with pytest.raises(DataSourceError, match="not a mix"):
        datasource.add_uploads([("films.db", io.BytesIO(b"")), ("cities.csv", io.BytesIO(b"a,b\n1,2\n"))])


def test_filename_cannot_escape_the_staging_directory(registry):
    source = registry / "real.db"
    _films_database(source)
    with source.open("rb") as handle:
        datasource.add_uploads([("../../../evil.db", handle)])
    staged = list((registry / "uploads").rglob("*"))
    assert [path.name for path in staged if path.is_file()] == ["evil.db"]
    assert not (registry / "evil.db").exists()


def test_csv_headers_become_safe_identifiers(registry):
    csv = io.BytesIO(b"City Name,Total Sales,Blank\nKarachi,1200,\nLahore,900,\n")
    record = datasource.add_uploads([("Cities Data.csv", csv)])
    assert record["tables"] == ["cities_data"]

    settings = datasource._settings_for(datasource._read_registry()["sources"][0])
    assert settings.allowed_tables == ["cities_data"]
    assert set(settings.hidden_columns) == set()

    result = execute_query("SELECT * FROM cities_data", settings)
    assert result.error is None
    assert result.columns == ["city_name", "total_sales"]
    assert result.rows[0] == ["Karachi", 1200]


def test_dump_replays_only_create_and_insert(registry):
    dump = (
        "CREATE TABLE tickets (id INTEGER PRIMARY KEY, status TEXT, amount REAL);\n"
        "INSERT INTO tickets VALUES (1,'open',10.0);\n"
        "CREATE INDEX idx_status ON tickets(status);\n"
        "DROP TABLE tickets;\n"
        "UPDATE tickets SET amount = 999;\n"
    ).encode()
    record = datasource.add_uploads([("shop.sql", io.BytesIO(dump))])

    assert record["tables"] == ["tickets"]
    assert any("Skipped 3" in note for note in record["notes"])

    settings = datasource._settings_for(datasource._read_registry()["sources"][0])
    surviving = execute_query("SELECT COUNT(*) AS row_count FROM tickets", settings)
    assert surviving.rows[0][0] == 1
    assert execute_query("UPDATE tickets SET amount = 1", settings).error


def test_empty_source_is_refused(registry):
    empty = registry / "empty.db"
    sqlite3.connect(empty).close()
    with empty.open("rb") as handle:
        with pytest.raises(DataSourceError, match="No tables were found"):
            datasource.add_uploads([("empty.db", handle)])
    assert datasource.list_sources()["sources"] == [datasource._demo_record()]


def test_connection_string_is_registered_but_never_returned(registry, monkeypatch):
    monkeypatch.setattr(datasource, "_probe", lambda url: None)
    monkeypatch.setattr(datasource, "discover_tables", lambda url: ["orders"])
    record = datasource.add_connection(
        "Shop replica", "postgresql://analyst:super-secret@warehouse.internal:5432/shop"
    )

    assert "super-secret" not in str(record)
    assert "url" not in record
    assert datasource.list_sources()["sources"][1]["origin"] == "postgresql://warehouse.internal:5432/shop"
    # The DSN has to live on disk for reconnection; the API response is what stays clean.
    assert "super-secret" in registry.joinpath("datasources.json").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("mongodb://host/db", "not a supported scheme"),
        ("sqlite:///tmp/x.db", "uploaded rather than pasted"),
        ("postgresql:///local_only", "no host"),
    ],
)
def test_bad_connection_strings_are_rejected(registry, url, reason):
    with pytest.raises(DataSourceError, match=reason):
        datasource.add_connection("", url)


# ------------------------------------------------------------------- activation
def test_activating_follows_the_uploaded_schema(registry):
    record = _upload_sqlite(registry)
    summary = datasource.activate(record["id"])

    assert summary["id"] == record["id"]
    assert summary["tables"] == 1

    settings = datasource.get_settings()
    assert settings.allowed_tables == ["films"]
    # A protected column of the sample warehouse must not shadow the new schema.
    assert settings.hidden_columns == []

    assert validate_sql("SELECT title, box_office FROM films", settings).ok
    assert not validate_sql("SELECT name FROM customers", settings).ok


def test_protection_still_applies_to_a_matching_table_name(registry, monkeypatch):
    monkeypatch.setattr(datasource, "base_settings", lambda: Settings(hidden_columns=["films.box_office"]))
    record = _upload_sqlite(registry)
    settings = datasource._settings_for(datasource._read_registry()["sources"][0])

    assert settings.hidden_columns == ["films.box_office"]
    assert not validate_sql("SELECT box_office FROM films", settings).ok
    assert not validate_sql("SELECT * FROM films", settings).ok


def test_deactivating_restores_the_sample_warehouse(registry):
    record = _upload_sqlite(registry)
    datasource.activate(record["id"])
    summary = datasource.activate(datasource.DEMO_ID)

    assert summary["id"] == "demo"
    assert datasource.get_settings().datasource_name == "Sample warehouse"


def test_resume_falls_back_when_a_saved_file_is_gone(registry):
    record = _upload_sqlite(registry)
    datasource.activate(record["id"])
    for path in (registry / "uploads").rglob("*.db"):
        path.unlink()

    assert datasource.resume_active() is None
    assert datasource.active_id() == "demo"


def test_remove_deletes_the_staged_files_and_the_record(registry):
    record = _upload_sqlite(registry)
    datasource.activate(record["id"])
    result = datasource.remove(record["id"])

    assert result["active"]["id"] == "demo"
    assert datasource.list_sources()["sources"] == [datasource._demo_record()]
    assert not (registry / "uploads" / record["id"]).exists()
    with pytest.raises(DataSourceError):
        datasource.remove("ds_does_not_exist")


def test_demo_record_cannot_be_deleted(registry):
    with pytest.raises(DataSourceError, match="built in"):
        datasource.remove(datasource.DEMO_ID)
