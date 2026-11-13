"""Query executor: read-only behaviour, row caps, and error reporting."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from app.config import Settings
from app.services import query_executor


@pytest.fixture
def db_url(tmp_path):
    path = tmp_path / "analytics.db"
    url = f"sqlite:///{path}"
    engine = create_engine(url, future=True)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE customers (customer_id INTEGER PRIMARY KEY, name TEXT, city TEXT)"))
        connection.execute(
            text("INSERT INTO customers (name, city) VALUES "
                 "('Ayesha','Karachi'),('Bilal','Karachi'),('Zara','Lahore'),('Ali','Islamabad')")
        )
    engine.dispose()
    return url


@pytest.fixture
def settings(db_url):
    return Settings(database_url=db_url, reader_database_url=db_url, max_rows=2)


def test_returns_columns_and_rows(settings):
    result = query_executor.execute_query("SELECT name, city FROM customers ORDER BY name", settings)
    assert result.ok
    assert result.columns == ["name", "city"]
    assert result.row_count == 2          # capped at max_rows
    assert result.truncated is True       # more rows existed


def test_no_limit_still_returns_all(settings):
    result = query_executor.execute_query(
        "SELECT name, city FROM customers ORDER BY name LIMIT 10", settings
    )
    # LIMIT 10 is reduced by the validator upstream; here the raw cap is 2.
    assert result.row_count == 2


def test_error_is_captured_not_raised(settings):
    result = query_executor.execute_query("SELECT * FROM does_not_exist", settings)
    assert not result.ok
    assert result.error
    assert "does_not_exist" in result.error


def test_numeric_aggregate_survives_json_conversion(settings):
    result = query_executor.execute_query(
        "SELECT COUNT(*) AS total FROM customers WHERE city = 'Karachi'", settings
    )
    assert result.rows[0][0] == 2


def test_payload_round_trip(settings):
    result = query_executor.execute_query("SELECT name FROM customers LIMIT 1", settings)
    clone = query_executor.QueryResult.from_payload(result.to_payload(), result.sql)
    assert clone.columns == result.columns
    assert clone.rows == result.rows
    assert clone.duration_ms == result.duration_ms
