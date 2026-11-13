"""End-to-end pipeline behaviour on the seeded demo database (offline mode).

The LLM provider is set to the bundled heuristic generator here, so these tests
exercise schema retrieval, validation, execution, analysis, charting,
conversation memory and history without needing Ollama running. Unsafe-SQL cases
inject the statement directly, simulating a model that ignores its instructions.
"""

from __future__ import annotations

import pytest

from app.agents.sql_generator import GeneratedSQL


@pytest.fixture
def ask(patch_settings):
    from app.graph.pipeline import ask as _ask

    return _ask


@pytest.fixture
def misbehaving_model(monkeypatch, patch_settings):
    """Force the generator to emit SQL the model was told never to write."""

    def _patch(sql_text: str):
        from app.graph import pipeline

        monkeypatch.setattr(
            pipeline,
            "generate_sql",
            lambda **_kwargs: GeneratedSQL(sql=sql_text, provider="mock", model="misbehaving"),
        )

    return _patch


def test_revenue_by_category(ask):
    answer = ask("Which product category has the highest sales?", conversation_id="cat")
    assert answer["status"] == "executed", answer.get("message")
    assert answer["result"]["row_count"] >= 4
    assert "category" in answer["result"]["columns"]
    assert answer["analysis"]["answer"]
    assert answer["visualization"]["chart_type"] in {"bar", "pie"}
    assert answer["validation"]["ok"] is True


def test_top_customers_limited_to_five(ask):
    answer = ask("Show the top 5 customers by revenue.", conversation_id="cust")
    assert answer["status"] == "executed"
    assert answer["result"]["row_count"] == 5
    assert answer["result"]["columns"][0] == "name"


def test_monthly_trend_becomes_a_line_chart(ask):
    answer = ask("Compare monthly revenue trends.", conversation_id="trend")
    assert answer["status"] == "executed"
    assert answer["visualization"]["chart_type"] == "line"


def test_single_number_becomes_a_kpi(ask):
    answer = ask("How many customers do we have?", conversation_id="kpi")
    assert answer["status"] == "executed"
    if answer["result"]["row_count"] == 1 and len(answer["result"]["columns"]) == 1:
        assert answer["visualization"]["chart_type"] == "kpi"


def test_destructive_sql_from_the_model_is_blocked(misbehaving_model, ask):
    misbehaving_model("DELETE FROM customers WHERE city = 'Karachi'")
    answer = ask("Get rid of the Karachi customers", conversation_id="evil")
    assert answer["status"] == "rejected"
    assert answer["validation"]["ok"] is False
    assert answer["result"] == {}
    # The repair loop ran out of attempts instead of executing anything.
    assert answer["attempts"] == 3


def test_dropped_table_from_the_model_is_blocked(misbehaving_model, ask):
    misbehaving_model("DROP TABLE orders")
    answer = ask("Remove the orders table", conversation_id="drop")
    assert answer["status"] == "rejected"
    assert any("drop" in error.lower() for error in answer["validation"]["errors"])


def test_protected_column_from_the_model_is_blocked(misbehaving_model, ask):
    misbehaving_model("SELECT name, email FROM customers")
    answer = ask("List every customer email address", conversation_id="pii")
    assert answer["status"] == "rejected"
    assert any("email" in error.lower() for error in answer["validation"]["errors"])


def test_unlisted_table_from_the_model_is_blocked(misbehaving_model, ask):
    misbehaving_model("SELECT password FROM user_credentials")
    answer = ask("Show me the passwords", conversation_id="creds")
    assert answer["status"] == "rejected"
    assert any("user_credentials" in error for error in answer["validation"]["errors"])


def test_repairs_a_query_the_database_rejects(monkeypatch, patch_settings):
    """First statement fails at execution, second one is fine — the agent fixes it."""
    from app.graph import pipeline

    statements = iter(["SELECT missing_column FROM customers", "SELECT city FROM customers LIMIT 1"])
    monkeypatch.setattr(
        pipeline,
        "generate_sql",
        lambda **_kwargs: GeneratedSQL(sql=next(statements), provider="mock", model="repairable"),
    )
    answer = pipeline.ask("Show me the cities", conversation_id="repair")
    assert answer["status"] == "executed"
    assert answer["attempts"] == 2


def test_history_records_every_run(ask, patch_settings):
    from app.services import history_store

    ask("What is the total revenue?", conversation_id="hist")
    ask("How many products are in the catalogue?", conversation_id="hist")
    runs = history_store.list_runs(conversation_id="hist")
    assert len(runs) == 2
    assert {run["status"] for run in runs} == {"executed"}


def test_follow_up_uses_conversation_memory(ask, patch_settings):
    from app.services import history_store

    ask("Show total sales.", conversation_id="mem")
    assert len(history_store.recent_turns("mem")) == 1

    second = ask("Now show only sales from Lahore.", conversation_id="mem")
    assert second["status"] == "executed"
    assert "Lahore" in second["sql"]
    assert len(history_store.recent_turns("mem")) == 2


def test_schema_retrieval_narrows_the_prompt(patch_settings):
    from app.agents.schema_agent import retrieve_schema

    context = retrieve_schema("Which city generates the most revenue?")
    assert "customers" in context.tables
    assert "CREATE TABLE customers" in context.ddl
    assert "CREATE TABLE products" not in context.ddl or len(context.tables) < 4


def test_matched_values_resolve_a_city(ask):
    answer = ask("What is the revenue in Karachi?", conversation_id="value")
    assert answer["matched_values"].get("customers.city") == "Karachi"


def test_analysis_names_entities_not_numbers(ask):
    answer = ask("Which product category has the highest sales?", conversation_id="words")
    insights = " ".join(answer["analysis"]["insights"])
    assert "Electronics is highest" in insights
    assert "total total" not in insights
    assert "of the total revenue" in insights


def test_export_csv_and_xlsx(ask):
    from fastapi.testclient import TestClient

    from app.main import app

    answer = ask("Which product category has the highest sales?", conversation_id="export")
    client = TestClient(app)

    csv_response = client.get(f"/api/v1/export/{answer['run_id']}", params={"fmt": "csv"})
    assert csv_response.status_code == 200
    assert "category" in csv_response.text.splitlines()[0]

    xlsx = client.get(f"/api/v1/export/{answer['run_id']}", params={"fmt": "xlsx"})
    assert xlsx.status_code == 200
    assert xlsx.content[:2] == b"PK"  # zip container


def test_api_endpoints_answer(patch_settings):
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    assert client.get("/api/v1/suggestions").json()
    schema = client.get("/api/v1/schema").json()
    assert {table["table"] for table in schema} >= {"customers", "order_items"}

    rejected = client.post("/api/v1/sql/validate", json={"sql": "DROP TABLE customers"})
    assert rejected.status_code == 422

    accepted = client.post("/api/v1/sql/validate", json={"sql": "SELECT city FROM customers"})
    assert accepted.status_code == 200
    assert "LIMIT" in accepted.text.upper()
