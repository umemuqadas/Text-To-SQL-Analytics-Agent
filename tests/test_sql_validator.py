"""Safety rules for the SQL validator.

These tests pin the guarantees the whole project rests on: nothing but a
single, allow-listed SELECT ever reaches the executor.
"""

from __future__ import annotations

import pytest

from app.config import Settings
from app.services.sql_validator import validate_sql


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url="sqlite:///:memory:",
        allowed_tables=["customers", "products", "orders", "order_items"],
        hidden_columns=["customers.email"],
        max_rows=100,
    )


def sql(text: str, settings: Settings):
    return validate_sql(text, settings=settings)


def test_plain_select_is_accepted(settings):
    report = sql("SELECT city FROM customers", settings)
    assert report.ok is True
    assert "customers" in report.tables


def test_select_with_aggregate_passes(settings):
    report = sql(
        "SELECT category, SUM(quantity * unit_price) AS total FROM order_items GROUP BY category",
        settings,
    )
    assert report.ok is True


def test_delete_is_blocked(settings):
    report = sql("DELETE FROM customers WHERE city = 'Karachi'", settings)
    assert report.ok is False
    assert any("not allowed" in error.lower() or "read-only" in error.lower() for error in report.errors)


def test_drop_is_blocked(settings):
    report = sql("DROP TABLE customers", settings)
    assert report.ok is False


def test_insert_and_update_are_blocked(settings):
    assert sql("INSERT INTO customers (name) VALUES ('x')", settings).ok is False
    assert sql("UPDATE customers SET city = 'Lahore'", settings).ok is False


def test_multiple_statements_are_blocked(settings):
    report = sql("SELECT 1 FROM customers; DROP TABLE customers", settings)
    assert report.ok is False
    assert any("one statement" in error.lower() for error in report.errors)


def test_unlisted_table_is_blocked(settings):
    report = sql("SELECT password FROM users", settings)
    assert report.ok is False
    assert any("users" in error.lower() for error in report.errors)


def test_hidden_column_is_blocked(settings):
    report = sql("SELECT name, email FROM customers", settings)
    assert report.ok is False
    assert any("email" in error.lower() for error in report.errors)


def test_hidden_column_is_blocked_through_a_table_alias(settings):
    report = sql("SELECT c.email FROM customers AS c", settings)
    assert report.ok is False
    assert any("email" in error.lower() for error in report.errors)


def test_same_column_name_on_another_table_is_not_blocked(settings):
    report = sql("SELECT quantity, unit_price FROM order_items", settings)
    assert report.ok is True


def test_select_star_blocked_when_table_has_protected_column(settings):
    report = sql("SELECT * FROM customers", settings)
    assert report.ok is False
    assert any("select *" in error.lower() for error in report.errors)


def test_row_limit_is_added(settings):
    report = sql("SELECT city FROM customers", settings)
    assert "LIMIT" in report.sql.upper()
    assert any("limit" in warning.lower() for warning in report.warnings)


def test_oversized_limit_is_reduced(settings):
    report = sql("SELECT city FROM customers LIMIT 99999", settings)
    assert report.ok is True
    assert "100" in report.sql
    assert any("reduced" in warning.lower() for warning in report.warnings)


def test_cte_is_allowed_and_alias_not_treated_as_table(settings):
    report = sql(
        "WITH item_total AS (SELECT order_id, quantity * unit_price AS total FROM order_items) "
        "SELECT order_id, total FROM item_total",
        settings,
    )
    assert report.ok is True
    assert "item_total" not in report.tables


def test_pascal_case_chained_ctes_are_still_local_aliases(settings):
    """Models capitalise CTE names and chain them; SQLGlot keeps the original case."""
    report = sql(
        "WITH CategoryRevenue AS (SELECT category, SUM(quantity * unit_price) AS total_revenue "
        "FROM order_items JOIN products ON order_items.product_id = products.product_id "
        "GROUP BY category), "
        "TopCategory AS (SELECT category, RANK() OVER (ORDER BY total_revenue DESC) AS r "
        "FROM CategoryRevenue) "
        "SELECT category FROM TopCategory WHERE r = 1",
        settings,
    )
    assert report.errors == []
    assert report.ok is True
    assert set(report.tables) == {"order_items", "products"}


def test_dangerous_function_is_blocked(settings):
    report = sql("SELECT city, pg_sleep(10) FROM customers", settings)
    assert report.ok is False


def test_empty_input_is_rejected(settings):
    assert sql("", settings).ok is False
    assert sql("   ", settings).ok is False


def test_unparseable_sql_is_rejected(settings):
    report = sql("SELECT FROM WHERE", settings)
    assert report.ok is False
