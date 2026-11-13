"""Visualization Agent — picks the chart the question actually deserves.

Rules, not guesses: a time-ish X axis becomes a line, a share question becomes
a pie, a single number becomes a KPI card, one categorical column with a metric
becomes a bar chart, anything else stays a table. The figure is returned as
plotly JSON so the React front end renders it directly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import plotly.graph_objects as go

from app.services.query_executor import QueryResult

_TIME_HINTS = ("month", "date", "year", "day", "week", "quarter", "trend", "time")
_PIE_HINTS = ("share", "percentage", "distribution", "split", "proportion", "mix", "category")


@dataclass(slots=True)
class Visualization:
    chart_type: str
    title: str
    reason: str
    figure: dict | None = None
    kpi: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "chart_type": self.chart_type,
            "title": self.title,
            "reason": self.reason,
            "figure": self.figure,
            "kpi": self.kpi,
        }


def build_visualization(question: str, result: QueryResult) -> Visualization:
    if not result.ok or result.row_count == 0:
        return Visualization("table", "No data", "Nothing to plot.")

    numeric = _numeric_columns(result)
    categorical = [name for name in result.columns if name not in numeric]
    lowered = question.lower()

    if result.row_count == 1 and len(numeric) == 1:
        value = result.rows[0][result.columns.index(numeric[0])]
        return Visualization(
            chart_type="kpi",
            title=_pretty(numeric[0]),
            reason="A single number is clearest as a KPI card.",
            kpi={"label": _pretty(numeric[0]), "value": value, "formatted": _fmt(value)},
        )

    if not numeric or not categorical:
        return Visualization("table", "Result set", "No categorical + numeric pair to chart.")

    x = _pick_axis(categorical, result)
    y = _pick_metric(numeric, lowered)

    if _is_time_axis(x, result):
        return _line(result, x, y)
    if any(hint in lowered for hint in _PIE_HINTS) and result.row_count <= 8:
        return _pie(result, x, y)
    if result.row_count <= 25:
        return _bar(result, x, y)
    return Visualization("table", "Result set", "Too many categories to chart readably; showing the table.")


# --------------------------------------------------------------------- builders
def _bar(result: QueryResult, x: str, y: str) -> Visualization:
    fig = go.Figure(
        go.Bar(
            x=[row[result.columns.index(x)] for row in result.rows],
            y=[row[result.columns.index(y)] for row in result.rows],
            marker_color="#6366f1",
        )
    )
    _style(fig, _pretty(y), _pretty(x))
    return Visualization("bar", f"{_pretty(y)} by {_pretty(x)}", "Comparing values across categories.", fig.to_plotly_json())


def _line(result: QueryResult, x: str, y: str) -> Visualization:
    fig = go.Figure(
        go.Scatter(
            x=[row[result.columns.index(x)] for row in result.rows],
            y=[row[result.columns.index(y)] for row in result.rows],
            mode="lines+markers",
            line={"color": "#0ea5e9", "width": 2},
            marker={"size": 6},
        )
    )
    _style(fig, _pretty(y), _pretty(x))
    return Visualization("line", f"{_pretty(y)} over {_pretty(x)}", "The X axis is a time bucket, so a trend line fits.", fig.to_plotly_json())


def _pie(result: QueryResult, x: str, y: str) -> Visualization:
    fig = go.Figure(
        go.Pie(
            labels=[row[result.columns.index(x)] for row in result.rows],
            values=[row[result.columns.index(y)] for row in result.rows],
            hole=0.45,
        )
    )
    fig.update_layout(template="plotly_white", margin={"l": 20, "r": 20, "t": 40, "b": 20}, title=_pretty(y))
    return Visualization("pie", f"{_pretty(y)} distribution", "The question asks how the total splits across categories.", fig.to_plotly_json())


def _style(fig: go.Figure, y_title: str, x_title: str) -> None:
    fig.update_layout(
        template="plotly_white",
        margin={"l": 60, "r": 20, "t": 40, "b": 60},
        xaxis_title=x_title,
        yaxis_title=y_title,
        height=340,
    )


# ---------------------------------------------------------------- detection
def _numeric_columns(result: QueryResult) -> list[str]:
    columns: list[str] = []
    for index, name in enumerate(result.columns):
        values = [row[index] for row in result.rows if row[index] is not None]
        if values and all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in values):
            columns.append(name)
    return columns


def _pick_axis(categorical: list[str], result: QueryResult) -> str:
    for name in categorical:
        if any(hint in name.lower() for hint in _TIME_HINTS):
            return name
    return categorical[0]


def _pick_metric(numeric: list[str], lowered: str) -> str:
    for keyword in ("revenue", "sales", "total", "amount", "count", "quantity"):
        for name in numeric:
            if keyword in name.lower():
                return name
    for name in numeric:
        if not name.lower().endswith("_id") and name.lower() != "id":
            return name
    return numeric[0]


def _is_time_axis(name: str, result: QueryResult) -> bool:
    if any(hint in name.lower() for hint in _TIME_HINTS):
        return True
    values = [str(row[result.columns.index(name)]) for row in result.rows[:5]]
    return all(re.match(r"^\d{4}(-\d{2})?", value) for value in values)


def _pretty(column: str) -> str:
    return column.replace("_", " ").strip().title()


def _fmt(value: object) -> str:
    if isinstance(value, (int, float)):
        return f"{value:,.2f}" if isinstance(value, float) else f"{value:,}"
    return str(value)
