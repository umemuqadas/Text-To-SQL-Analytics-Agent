"""The offline heuristic generator running against a schema it was never told about."""

from __future__ import annotations

import sqlite3

import pytest

from app.agents.schema_agent import get_catalog, retrieve_schema
from app.agents.sql_generator import is_commerce_dataset, offline_sql, suggested_questions
from app.config import Settings
from app.services.query_executor import execute_query
from app.services.sql_validator import validate_sql

GENRES = [
    ("RRR", "Action", "Telugu", 2022, 145000000.0, 187),
    ("Lagaan", "Drama", "Hindi", 2001, 6500000.0, 224),
    ("3 Idiots", "Comedy", "Hindi", 2009, 46000000.0, 171),
    ("Baahubali", "Action", "Telugu", 2015, 181000000.0, 159),
]


@pytest.fixture
def films_settings(tmp_path):
    """A SQLite file with one table and no idea what anyone will ask about it."""
    raw = sqlite3.connect(tmp_path / "films.db")
    raw.executescript(
        "CREATE TABLE films (film_id INTEGER PRIMARY KEY, title TEXT, genre TEXT,"
        " language TEXT, release_year INTEGER, box_office REAL, runtime_min INTEGER);"
    )
    raw.executemany("INSERT INTO films VALUES (?,?,?,?,?,?,?)", [(i, *row) for i, row in enumerate(GENRES, 1)])
    raw.commit()
    raw.close()

    return Settings(
        database_url=f"sqlite:///{tmp_path / 'films.db'}",
        history_db_url=f"sqlite:///{tmp_path / 'history.db'}",
        llm_provider="heuristic",
        allowed_tables=["films"],
        hidden_columns=[],
        max_rows=50,
        datasource_id="films_test",
        datasource_kind="sqlite",
    )


def _answer(question: str, settings: Settings):
    """Generate, validate and run — the same three steps the pipeline takes."""
    context = retrieve_schema(question, settings)
    sql = offline_sql(question, settings, get_catalog(settings), context.matched_values)
    report = validate_sql(sql, settings)
    assert report.ok, f"{question!r} produced a rejected query: {sql}\n{report.errors}"
    result = execute_query(sql, settings)
    assert result.error is None, f"{question!r} failed to run: {sql}\n{result.error}"
    return sql, result


@pytest.mark.parametrize(
    ("question", "expect"),
    [
        ("How many films are in the database?", "COUNT(*)"),
        ("What was the total box office?", "SUM(box_office)"),
        ("Average runtime by genre?", "AVG(runtime_min)"),
        ("Which genres have the most films?", "GROUP BY"),
        ("Show me the films", "FROM films"),
    ],
)
def test_generic_questions_run(films_settings, question, expect):
    sql, _ = _answer(question, films_settings)
    assert expect in sql


def test_no_commerce_table_is_invented(films_settings):
    """The heuristics must not reach for orders/customers that do not exist here."""
    for question in ("total revenue", "best selling product", "how many customers"):
        sql, result = _answer(question, films_settings)
        assert "orders" not in sql.lower()
        assert result.error is None


def test_a_real_money_column_beats_a_numeric_one(films_settings):
    sql, _ = _answer("What was the total box office?", films_settings)
    assert "SUM(box_office) AS total_box_office" in sql
    assert "release_year" not in sql


def test_values_from_the_schema_become_filters(films_settings):
    sql, result = _answer("How many Telugu films are there?", films_settings)
    assert "WHERE language = 'Telugu'" in sql
    assert result.rows[0] == [2]


def test_how_much_aggregates_instead_of_counting(films_settings):
    sql, result = _answer("How much box office did Telugu films earn?", films_settings)
    assert "SUM(box_office) AS total_box_office" in sql
    assert "WHERE language = 'Telugu'" in sql
    assert result.rows[0] == [326000000.0]


def test_aggregate_ranking_is_ordered_and_capped(films_settings):
    sql, result = _answer("How many films are there per genre?", films_settings)
    assert "COUNT(*) AS row_count" in sql
    assert "ORDER BY row_count DESC" in sql
    assert "LIMIT" in sql
    assert result.rows[0][0] == "Action"
    assert {row[0] for row in result.rows} == {"Action", "Comedy", "Drama"}


def test_a_named_category_beats_the_default_label(films_settings):
    sql, _ = _answer("Average runtime by genre?", films_settings)
    assert "GROUP BY genre" in sql
    assert "AVG(runtime_min) AS avg_runtime_min" in sql


def test_a_lowest_question_sorts_the_other_way(films_settings):
    sql, result = _answer("Which films had the lowest box office?", films_settings)
    assert "GROUP BY title" in sql
    assert "ORDER BY total_box_office ASC" in sql
    assert result.rows[0][0] == "Lagaan"


def test_a_listing_question_returns_rows(films_settings):
    sql, result = _answer("Show me the films", films_settings)
    assert result.row_count == len(GENRES)
    assert "film_id" in result.columns


def test_suggestions_only_reference_tables_that_exist(films_settings):
    catalog = get_catalog(films_settings)
    questions = suggested_questions(catalog)

    assert questions
    for question in questions:
        _answer(question, films_settings)


def test_the_commerce_dataset_keeps_the_warehouse_heuristics():
    from app.config import base_settings

    assert is_commerce_dataset(base_settings())
    assert not is_commerce_dataset(Settings(allowed_tables=["films"]))
