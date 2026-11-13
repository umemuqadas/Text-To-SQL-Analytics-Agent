"""SQL Generator — natural language to a single SELECT statement.

Uses Ollama (Qwen2.5-Coder by default) when it is reachable. If it is not, the
bundled rule-based generator produces a query instead, so the whole pipeline —
validation, execution, analysis, charting — stays exercisable offline. The
provider actually used is reported with every answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.agents.schema_agent import get_catalog
from app.config import Settings, get_settings
from app.database.schema import SchemaCatalog, TableInfo
from app.services.llm_client import Completion, LLMUnavailableError, OllamaClient, get_llm_client

SYSTEM_PROMPT = """You are a precise SQL analyst for a read-only analytics warehouse.

Rules you must follow:
1. Reply with exactly one SQL statement and nothing else.
2. Only SELECT (or WITH ... SELECT) queries are allowed.
3. Use only the tables and columns shown in the schema block. Never invent names.
4. {domain_rule}
5. Add explicit column aliases for every aggregate.
6. Prefer a JOIN over correlated subqueries.
7. Respect follow-up questions: reuse the shape of the previous query and change
   only what the new question asks for.
8. Use {dialect} syntax.
"""

_COMMERCE_DOMAIN_RULE = "Revenue is always SUM(order_items.quantity * order_items.unit_price)."
_GENERIC_DOMAIN_RULE = (
    "This schema was uploaded by a user: treat the table and column names literally, "
    "and never assume a business definition that is not in the schema block."
)

# The sample warehouse gets hand-tuned rules; anything else uses the generic ones.
_COMMERCE_TABLES = {"orders", "order_items", "products", "customers"}


@dataclass(slots=True)
class GeneratedSQL:
    sql: str
    provider: str
    model: str
    latency_ms: int = 0
    raw: str = ""
    note: str | None = None


def generate_sql(
    question: str,
    schema_ddl: str,
    context_turns: list[dict] | None = None,
    error_feedback: str | None = None,
    settings: Settings | None = None,
    client: OllamaClient | None = None,
    catalog: SchemaCatalog | None = None,
    matched_values: dict[str, str] | None = None,
) -> GeneratedSQL:
    settings = settings or get_settings()
    prompt = build_prompt(question, schema_ddl, context_turns, error_feedback, settings)
    system = system_prompt(settings)

    if settings.llm_provider == "ollama":
        try:
            completion: Completion = (client or get_llm_client()).complete(system, prompt)
            sql = extract_sql(completion.text)
            if sql:
                return GeneratedSQL(
                    sql=sql,
                    provider=completion.provider,
                    model=completion.model,
                    latency_ms=completion.latency_ms,
                    raw=completion.text,
                )
            note = "Model response contained no SQL; used the offline generator."
        except LLMUnavailableError as exc:
            note = f"Ollama unavailable ({exc}); used the offline generator."
    else:
        note = "Offline heuristic generator (set LLM_PROVIDER=ollama for the LLM path)."

    sql = offline_sql(question, settings, catalog=catalog, matched_values=matched_values)
    return GeneratedSQL(sql=sql, provider="heuristic", model="rules-v1", note=note)


def is_commerce_dataset(settings: Settings) -> bool:
    """True for the bundled sample warehouse, which has hand-written rules."""
    return _COMMERCE_TABLES <= {table.lower() for table in settings.allowed_tables}


def system_prompt(settings: Settings) -> str:
    return SYSTEM_PROMPT.format(
        dialect=_dialect_label(settings),
        domain_rule=_COMMERCE_DOMAIN_RULE if is_commerce_dataset(settings) else _GENERIC_DOMAIN_RULE,
    )


def build_prompt(
    question: str,
    schema_ddl: str,
    context_turns: list[dict] | None,
    error_feedback: str | None,
    settings: Settings,
) -> str:
    blocks = [f"### Database schema\n{schema_ddl.strip()}", f"### Question\n{question.strip()}"]
    if context_turns:
        history = "\n".join(
            f"Q: {turn['question']}\nSQL: {turn['sql']}" for turn in context_turns
        )
        blocks.append(f"### Previous turns (for context)\n{history}")
    if error_feedback:
        blocks.append(
            f"### The previous attempt failed\n{error_feedback.strip()}\n"
            "Fix it and return corrected SQL only."
        )
    blocks.append(f"### Instructions\nReturn one { _dialect_label(settings) } SELECT query.")
    return "\n\n".join(blocks)


def _dialect_label(settings: Settings) -> str:
    return "PostgreSQL" if not settings.is_sqlite else "SQLite"


def extract_sql(text_response: str) -> str:
    """Pull the statement out of markdown fences, prose, or a raw string."""
    if not text_response:
        return ""
    fenced = re.search(r"```(?:sql)?\s*(.+?)```", text_response, re.IGNORECASE | re.DOTALL)
    candidate = fenced.group(1) if fenced else text_response
    candidate = candidate.strip().rstrip(";").strip()
    if not candidate:
        return ""
    if not fenced:
        match = re.search(r"\b(with|select)\b.+", candidate, re.IGNORECASE | re.DOTALL)
        if not match:
            return ""
        candidate = match.group(0).strip().rstrip(";")
    # A model that ignores "one statement only" still gets the first query.
    return candidate.split(";")[0].strip()


# --------------------------------------------------------------------- offline
_ORDER_JOIN = (
    "FROM orders o\n"
    "JOIN customers c ON c.customer_id = o.customer_id\n"
    "JOIN order_items oi ON oi.order_id = o.order_id"
)
_REVENUE = "SUM(oi.quantity * oi.unit_price)"


def offline_sql(
    question: str,
    settings: Settings,
    catalog: SchemaCatalog | None = None,
    matched_values: dict[str, str] | None = None,
) -> str:
    """Deterministic question→SQL rules used when no LLM is available."""
    if is_commerce_dataset(settings):
        return _commerce_sql(question, settings)
    return _generic_sql(question, catalog or get_catalog(settings), matched_values or {}, settings)


def _commerce_sql(question: str, settings: Settings) -> str:
    lowered = question.lower()
    month_expr = "strftime('%Y-%m', o.order_date)" if settings.is_sqlite else "to_char(o.order_date, 'YYYY-MM')"
    cutoff = "DATE('now', '-90 days')" if settings.is_sqlite else "(CURRENT_DATE - INTERVAL '90 days')"
    city = _match_city(question)
    city_where = f"WHERE c.city = {_quote_literal(city)}\n" if city else ""
    limit = _extract_limit(lowered) or 10

    if any(word in lowered for word in ("inactive", "not placed", "no order", "90 days")):
        order_by = "" if settings.is_sqlite else "\nORDER BY last_order_date"
        return (
            "SELECT c.customer_id, c.name, c.city, MAX(o.order_date) AS last_order_date\n"
            "FROM customers c\n"
            "LEFT JOIN orders o ON o.customer_id = c.customer_id\n"
            "GROUP BY c.customer_id, c.name, c.city\n"
            f"HAVING MAX(o.order_date) IS NULL OR MAX(o.order_date) < {cutoff}{order_by}"
        )

    if "categor" in lowered:
        return (
            "SELECT p.category,\n"
            f"       {_REVENUE} AS total_revenue\n"
            "FROM order_items oi\n"
            "JOIN orders o ON o.order_id = oi.order_id\n"
            "JOIN products p ON p.product_id = oi.product_id\n"
            "JOIN customers c ON c.customer_id = o.customer_id\n"
            f"{city_where}"
            "GROUP BY p.category\n"
            "ORDER BY total_revenue DESC"
        )

    if "customer" in lowered and any(word in lowered for word in ("top", "best", "highest", "revenue", "ranking")):
        return (
            "SELECT c.name,\n"
            "       c.city,\n"
            f"       {_REVENUE} AS total_revenue\n"
            f"{_ORDER_JOIN}\n"
            "WHERE o.status <> 'cancelled'\n"
            "GROUP BY c.name, c.city\n"
            "ORDER BY total_revenue DESC\n"
            f"LIMIT {limit}"
        )

    if any(word in lowered for word in ("product", "selling", "sold", "item")):
        return (
            "SELECT p.product_name,\n"
            "       p.category,\n"
            f"       {_REVENUE} AS total_revenue\n"
            f"{_ORDER_JOIN}\n"
            "JOIN products p ON p.product_id = oi.product_id\n"
            "WHERE o.status <> 'cancelled'\n"
            "GROUP BY p.product_name, p.category\n"
            "ORDER BY total_revenue DESC\n"
            f"LIMIT {limit}"
        )

    if any(word in lowered for word in ("monthly", "trend", "per month", "by month", "over time")):
        return (
            f"SELECT {month_expr} AS month,\n"
            f"       {_REVENUE} AS total_revenue\n"
            f"{_ORDER_JOIN}\n"
            "WHERE o.status <> 'cancelled'\n"
            f"{city_where}"
            "GROUP BY month\n"
            "ORDER BY month"
        )

    if "cit" in lowered and not city:
        return (
            "SELECT c.city,\n"
            f"       {_REVENUE} AS total_revenue\n"
            f"{_ORDER_JOIN}\n"
            "WHERE o.status <> 'cancelled'\n"
            "GROUP BY c.city\n"
            "ORDER BY total_revenue DESC"
        )

    # A specific city was named, so scope the breakdown to it. History alone must
    # not change the shape of an already self-contained question.
    if city:
        return (
            "SELECT o.order_date,\n"
            "       c.city,\n"
            f"       {_REVENUE} AS total_revenue\n"
            f"{_ORDER_JOIN}\n"
            f"{city_where}"
            "GROUP BY o.order_date, c.city\n"
            "ORDER BY total_revenue DESC\n"
            f"LIMIT {limit}"
        )

    return (
        f"SELECT {_REVENUE} AS total_revenue\n"
        "FROM order_items oi\n"
        "JOIN orders o ON o.order_id = oi.order_id\n"
        "WHERE o.status <> 'cancelled'"
    )


def _match_city(question: str) -> str | None:
    match = re.search(r"\b(?:from|in|with|for)\s+([A-Z][a-z]+(?:[ -][A-Z][a-z]+)?)\b", question)
    return match.group(1) if match else None


# -------------------------------------------------------------------- generic
# Rules that need no prior knowledge of the schema, so an uploaded dataset is
# answerable offline: count rows, aggregate a measure, split by a category,
# filter by a value the question actually named.
_COUNT_WORDS = ("how many", "count", "number of", "total number")
_AVERAGE_WORDS = ("average", "mean", "avg")
_BOTTOM_WORDS = ("bottom", "worst", "lowest", "least", "smallest", "minimum")
_GROUP_WORDS = (" by ", " per ", " each ", "top", "best", "rank", "which", "share",
                "distribution", "breakdown", "split", "compare", "group")
_TIME_WORDS = ("month", "date", "day", "year", "week", "quarter", "trend", "over time")
_LIST_WORDS = ("show", "list", "display", "preview", "sample", "browse")

_NUMERIC_HINTS = ("INT", "NUM", "DEC", "FOAT", "DOUBLE", "REAL", "SERIAL", "MONEY")
_DECIMAL_HINTS = ("REAL", "FLOA", "DOUB", "DEC", "NUM", "MONEY")
_TEMPORAL_HINTS = ("DATE", "TIME", "TIMESTAM", "YEAR")
_TEXT_HINTS = ("CHAR", "TEXT", "ENUM", "CLOB")
# Numeric but never a business measure.
_MEASURE_STOPWORDS = ("year", "month", "day", "zip", "postal", "phone", "code", "id")
# Column names that usually hold the thing a row actually is.
_ENTITY_LABEL_HINTS = ("name", "title", "label")


def _generic_sql(
    question: str,
    catalog: SchemaCatalog,
    matched_values: dict[str, str],
    settings: Settings,
) -> str:
    lowered = question.lower()
    table = _pick_table(catalog, question)
    if table is None:
        return "SELECT 1 AS no_tables_available"

    where = _value_filter(table, matched_values)
    limit = _extract_limit(lowered) or 10
    measures = [c for c in table.columns if _is_measure(c)]
    measure = _pick_measure(measures, lowered)
    label = _best_match(_by_cardinality(table, [c for c in table.columns if _is_label(c)]), lowered)
    temporal = next((c for c in table.columns if _is_temporal(c)), None)

    wants_count = any(word in lowered for word in _COUNT_WORDS)
    wants_average = any(word in lowered for word in _AVERAGE_WORDS)
    wants_time = temporal is not None and any(word in lowered for word in _TIME_WORDS)
    wants_group = any(word in lowered for word in _GROUP_WORDS)
    direction = "ASC" if any(word in lowered for word in _BOTTOM_WORDS) else "DESC"

    if (wants_time and (measure or wants_count)) or (wants_group and (label or wants_count)):
        if wants_count or measure is None:
            aggregate, alias = "COUNT(*)", "row_count"
        else:
            function, prefix = ("AVG", "avg") if wants_average else ("SUM", "total")
            aggregate, alias = f"{function}({measure.name})", f"{prefix}_{measure.name}"

        if wants_time:
            bucket = _month_bucket(temporal, settings)
            head, group_by, order = f"{bucket} AS month", bucket, "ORDER BY month"
        else:
            head = group_by = label.name
            order = f"ORDER BY {alias} {direction}\nLIMIT {limit}"
        return f"SELECT {head},\n       {aggregate} AS {alias}\nFROM {table.name}\n{where}GROUP BY {group_by}\n{order}"

    if wants_count:
        return f"SELECT COUNT(*) AS row_count\nFROM {table.name}\n{where}".rstrip()

    if measure is not None and not _only_lists(lowered):
        function, prefix = ("AVG", "avg") if wants_average else ("SUM", "total")
        return f"SELECT {function}({measure.name}) AS {prefix}_{measure.name}\nFROM {table.name}\n{where}".rstrip()

    return _sample_rows(table, where, limit)


def _sample_rows(table: TableInfo, where: str, limit: int) -> str:
    columns = ", ".join(column.name for column in table.columns[:6])
    return f"SELECT {columns}\nFROM {table.name}\n{where}LIMIT {limit}".rstrip()


def _month_bucket(column, settings: Settings) -> str:
    if settings.is_sqlite:
        return f"strftime('%Y-%m', {column.name})"
    return f"to_char({column.name}, 'YYYY-MM')"


def suggested_questions(catalog: SchemaCatalog, limit: int = 6) -> list[str]:
    """Questions an unknown schema can actually answer, for the starter chips."""
    questions: list[str] = []
    for table in catalog.tables[:4]:
        subject = table.name.replace("_", " ")
        measures = [column for column in table.columns if _is_measure(column)]
        labels = _by_cardinality(table, [column for column in table.columns if _is_label(column)])
        questions.append(f"How many rows are in {subject}?")
        if measures:
            measure = _human(_pick_measure(measures, "").name)
            if labels:
                questions.append(f"Show the top 10 {subject} by {measure}.")
            else:
                questions.append(f"What is the total {measure}?")
        elif labels:
            questions.append(f"How are rows distributed by {_human(labels[0].name)}?")
    return questions[:limit]


def _human(column: str) -> str:
    return column.replace("_", " ").strip()


def _pick_table(catalog: SchemaCatalog, question: str) -> TableInfo | None:
    names = catalog.select_relevant_tables(question, limit=1)
    return catalog.table(names[0]) if names else None


def _value_filter(table: TableInfo, matched_values: dict[str, str]) -> str:
    """Turn a value the question named into a WHERE clause on this table."""
    conditions = []
    for qualified, value in matched_values.items():
        owner, _, column = qualified.partition(".")
        if owner == table.name and column in table.column_names:
            conditions.append(f"{column} = {_quote_literal(value)}")
    return f"WHERE {' AND '.join(conditions)}\n" if conditions else ""


def _only_lists(lowered: str) -> bool:
    return any(word in lowered for word in _LIST_WORDS) and not any(
        word in lowered for word in ("total", "sum", "average", "how many", "count", "top", "which")
    )


def _by_cardinality(table: TableInfo, columns) -> list:
    """Text columns with few distinct values first — they make readable groups."""
    def cardinality(column) -> int:
        return len(table.values.get(column.name, [])) or 10_000

    return sorted(columns, key=cardinality)


def _best_match(columns, lowered: str):
    """The column a stakeholder would read as the row's label.

    A name mentioned in the question wins outright; when nothing matches, a
    column that looks like a proper label beats any category with fewer values.
    """

    def score(column) -> int:
        return _name_score(column.name, lowered) * 3 + (
            2 if any(hint in column.name.lower() for hint in _ENTITY_LABEL_HINTS) else 0
        )

    return max(columns, key=score, default=None)


def _pick_measure(measures, lowered: str):
    """The column a stakeholder means when they say "the numbers".

    A name mentioned in the question wins; otherwise a decimal column is a far
    better guess than an integer one, which is usually a year or a code.
    """
    def score(column) -> int:
        return _name_score(column.name, lowered) * 3 + (2 if _is_decimal(column) else 0)

    return max(measures, key=score, default=None)


def _name_score(name: str, lowered: str) -> int:
    parts = [part for part in re.split(r"[^a-z0-9]+", name.lower()) if len(part) > 2]
    return sum(1 for part in parts if part in lowered) + (2 if name.lower() in lowered else 0)


def _is_measure(column) -> bool:
    if not _is_numeric(column) or column.primary_key or column.foreign_key:
        return False
    return not any(word in column.name.lower() for word in _MEASURE_STOPWORDS)


def _is_decimal(column) -> bool:
    return any(hint in column.data_type.upper() for hint in _DECIMAL_HINTS)


def _is_label(column) -> bool:
    return any(hint in column.data_type.upper() for hint in _TEXT_HINTS) and not column.primary_key


def _is_numeric(column) -> bool:
    return any(hint in column.data_type.upper() for hint in _NUMERIC_HINTS)


def _is_temporal(column) -> bool:
    return any(hint in column.data_type.upper() for hint in _TEMPORAL_HINTS)


def _quote_literal(value: str) -> str:
    # Single quotes only; doubles any embedded quote so the literal stays inert.
    return "'" + value.replace("'", "''") + "'"


def _extract_limit(lowered: str) -> int | None:
    match = re.search(r"(?:top|first|best)\s+(\d{1,3})", lowered)
    return int(match.group(1)) if match else None
