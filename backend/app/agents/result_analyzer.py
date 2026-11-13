"""Result Analyzer — turns rows back into an answer a human can act on."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.config import Settings, get_settings
from app.services.llm_client import LLMUnavailableError, OllamaClient, get_llm_client
from app.services.query_executor import QueryResult

SYSTEM_PROMPT = """You are a business analyst answering a stakeholder's question.
Explain what the numbers say in two or three plain sentences. No SQL, no jargon,
no invented figures — only use the rows you were given."""

_MAX_ROWS_IN_PROMPT = 25


@dataclass(slots=True)
class Analysis:
    answer: str
    insights: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    provider: str = "deterministic"


def analyze(
    question: str,
    result: QueryResult,
    settings: Settings | None = None,
    client: OllamaClient | None = None,
) -> Analysis:
    settings = settings or get_settings()

    if not result.ok:
        return Analysis(answer=f"The query could not be run: {result.error}", provider="deterministic")

    metrics = _metrics(result)
    fallback = Analysis(answer=_describe(question, result, metrics), insights=_insights(result, metrics))

    if settings.llm_provider != "ollama":
        return fallback

    prompt = _build_prompt(question, result, metrics)
    try:
        completion = (client or get_llm_client()).complete(SYSTEM_PROMPT, prompt)
    except LLMUnavailableError as exc:
        return Analysis(
            answer=fallback.answer,
            insights=fallback.insights + [f"LLM summary skipped: {exc}"],
            metrics=metrics,
            provider=fallback.provider,
        )
    return Analysis(
        answer=completion.text.strip(),
        insights=fallback.insights,
        metrics=metrics,
        provider=completion.provider,
    )


def _numeric_columns(result: QueryResult) -> list[str]:
    if not result.rows:
        return []
    columns: list[str] = []
    for index, name in enumerate(result.columns):
        values = [row[index] for row in result.rows if row[index] is not None]
        if values and all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in values):
            columns.append(name)
    return columns


def _metrics(result: QueryResult) -> dict:
    numeric = _numeric_columns(result)
    metrics: dict = {"row_count": result.row_count, "numeric_columns": numeric}
    for name in numeric[:3]:
        values = [row[result.columns.index(name)] for row in result.rows]
        metrics[f"total:{name}"] = sum(values)
        metrics[f"max:{name}"] = max(values)
        metrics[f"min:{name}"] = min(values)
        metrics[f"avg:{name}"] = round(sum(values) / len(values), 2) if values else 0
    return metrics


def _primary_metric(result: QueryResult) -> str | None:
    numeric = _numeric_columns(result)
    return numeric[0] if numeric else None


def _describe(question: str, result: QueryResult, metrics: dict) -> str:
    if result.row_count == 0:
        return "No rows matched this question, so there is nothing to report yet."

    metric = _primary_metric(result)
    if metric is None:
        first = result.rows[0]
        joined = ", ".join(str(value) for value in first)
        return f"The query returned {result.row_count} row(s). The first is: {joined}."

    if result.row_count == 1:
        value = result.rows[0][result.columns.index(metric)]
        return f"{_label(metric).capitalize()} was {_fmt(value)}."

    index = result.columns.index(metric)
    total = metrics[f"total:{metric}"]
    top_row = max(result.rows, key=lambda row: row[index] or 0)
    label_index = _label_index(result, index)
    share = (top_row[index] / total * 100) if total else 0
    return (
        f"Across {result.row_count} rows the {_label(metric)} is {_fmt(total)}. "
        f"{top_row[label_index]} leads with {_fmt(top_row[index])}, about {share:.0f}% of the total."
    )


def _insights(result: QueryResult, metrics: dict) -> list[str]:
    insights: list[str] = []
    metric = _primary_metric(result)
    if metric and result.row_count > 1:
        index = result.columns.index(metric)
        label_index = _label_index(result, index)
        total = metrics[f"total:{metric}"]
        top = max(result.rows, key=lambda row: row[index] or 0)
        bottom = min(result.rows, key=lambda row: row[index] or 0)
        insights.append(
            f"{top[label_index]} is highest at {_fmt(top[index])}; {bottom[label_index]} is lowest at {_fmt(bottom[index])}."
        )
        if total:
            spread = (top[index] - bottom[index]) / abs(total) * 100
            insights.append(f"Top-to-bottom spread is {spread:.0f}% of the {_label(metric)}.")
    if result.truncated:
        insights.append("The result set was capped by the row limit; narrow the question for a full view.")
    return insights


def _label_index(result: QueryResult, metric_index: int) -> int:
    """First column that names the row rather than measuring it."""
    return next(
        (i for i in range(len(result.columns)) if i != metric_index and not isinstance(result.rows[0][i], (int, float))),
        0,
    )


def _build_prompt(question: str, result: QueryResult, metrics: dict) -> str:
    header = " | ".join(result.columns)
    body = "\n".join(" | ".join("" if value is None else str(value) for value in row) for row in result.rows[:_MAX_ROWS_IN_PROMPT])
    tail = f"\n(showing {min(_MAX_ROWS_IN_PROMPT, result.row_count)} of {result.row_count} rows)" if result.row_count > _MAX_ROWS_IN_PROMPT else ""
    return (
        f"### Question\n{question}\n\n"
        f"### Result table\n{header}\n{body}{tail}\n\n"
        f"### Aggregates\n{metrics}\n\n"
        "Answer the question from this data."
    )


def _fmt(value: float | int | None) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:,.2f}"
    return f"{value:,}"


def _label(column: str) -> str:
    return column.replace("_", " ")
