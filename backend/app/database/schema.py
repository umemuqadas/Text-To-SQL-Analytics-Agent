"""Schema introspection and schema-aware retrieval.

The agent never shows the model the whole database. ``select_relevant_tables``
scores every table against the question and returns only the definitions the
model needs — cheaper prompts, fewer hallucinated columns, better JOINs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import inspect, text

from app.config import Settings, get_settings
from app.database.connection import get_engine

_STOPWORDS = {
    "the", "a", "an", "of", "for", "and", "or", "to", "in", "on", "is", "are", "what",
    "which", "show", "tell", "me", "how", "many", "much", "our", "we", "get", "give",
    "list", "find", "compare", "with", "by", "total", "all",
}

# Text-ish column types worth profiling for literal value matching.
_TEXT_TYPE_HINTS = ("CHAR", "TEXT", "ENUM", "CLOB")
_MAX_VALUE_CARDINALITY = 100
_MAX_PROFILED_ROWS = 50_000
_PROFILED_COLUMN_BUDGET = 60


def _quote(engine, identifier: str) -> str:
    return engine.dialect.identifier_preparer.quote(identifier)


@dataclass(slots=True)
class ColumnInfo:
    name: str
    data_type: str
    primary_key: bool = False
    foreign_key: str | None = None
    nullable: bool = True

    def as_ddl_fragment(self) -> str:
        parts = [f"{self.name} {self.data_type}"]
        if self.primary_key:
            parts.append("PRIMARY KEY")
        if self.foreign_key:
            parts.append(f"REFERENCES {self.foreign_key}")
        return " ".join(parts)


@dataclass(slots=True)
class TableInfo:
    name: str
    columns: list[ColumnInfo] = field(default_factory=list)
    row_count: int | None = None
    # Distinct values of low-cardinality text columns, used to match words in a
    # question to a real filter value ("only from Karachi" -> customers.city).
    values: dict[str, list[str]] = field(default_factory=dict)

    @property
    def column_names(self) -> list[str]:
        return [column.name for column in self.columns]

    def to_ddl(self) -> str:
        body = ",\n    ".join(column.as_ddl_fragment() for column in self.columns)
        return f"CREATE TABLE {self.name} (\n    {body}\n);"

    def value_domain(self) -> str:
        if not self.values:
            return ""
        pairs = "; ".join(
            f"{column} in ({', '.join(repr(value) for value in values[:12])})"
            for column, values in self.values.items()
        )
        return f"-- Distinct values in {self.name}: {pairs}"


class SchemaCatalog:
    """In-memory view of the allowed tables, rebuilt on demand."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.tables: list[TableInfo] = []

    def load(self, refresh: bool = False) -> "SchemaCatalog":
        if refresh or not self.tables:
            self.tables = self._introspect()
        return self

    def _introspect(self) -> list[TableInfo]:
        engine = get_engine("admin", self.settings)
        inspector = inspect(engine)
        visible = set(inspector.get_table_names()) | set(inspector.get_view_names())
        allowed = [name for name in self.settings.allowed_tables if name in visible]

        tables: list[TableInfo] = []
        for name in allowed:
            hidden = {
                column.split(".", 1)[1]
                for column in self.settings.hidden_columns
                if column.split(".", 1)[0] == name
            }
            pk = set(inspector.get_pk_constraint(name).get("constrained_columns", []))
            fks = {fk["constrained_columns"][0]: f"{fk['referred_table']}.{fk['referred_columns'][0]}"
                   for fk in inspector.get_foreign_keys(name) if fk["constrained_columns"]}
            columns = [
                ColumnInfo(
                    name=col["name"],
                    data_type=str(col["type"]),
                    primary_key=col["name"] in pk,
                    foreign_key=fks.get(col["name"]),
                    nullable=col.get("nullable", True),
                )
                for col in inspector.get_columns(name)
                if col["name"] not in hidden
            ]
            table = TableInfo(name=name, columns=columns, row_count=self._count_rows(engine, name))
            tables.append(table)
        self._profile_values(engine, tables)
        return tables

    @staticmethod
    def _count_rows(engine, name: str) -> int | None:
        try:
            with engine.connect() as connection:
                return int(connection.execute(text(f"SELECT COUNT(*) FROM {_quote(engine, name)}")).scalar_one())
        except Exception:  # noqa: BLE001 - counts are advisory only
            return None

    @staticmethod
    def _profile_values(engine, tables: list[TableInfo]) -> None:
        """Collect the value domain of small text columns so questions can match them.

        Keys, IDs and high-cardinality free text are skipped, and so is any table
        too big to scan for this: a warehouse with 200 million rows should not
        make the first question slow. Unprofiled columns simply never match.
        """
        budget = _PROFILED_COLUMN_BUDGET
        for table in tables:
            if table.row_count is None or table.row_count > _MAX_PROFILED_ROWS:
                continue
            for column in table.columns:
                if budget <= 0:
                    return
                budget -= 1
                if column.primary_key or column.foreign_key:
                    continue
                if not any(hint in column.data_type.upper() for hint in _TEXT_TYPE_HINTS):
                    continue
                query = (
                    f"SELECT DISTINCT {_quote(engine, column.name)} FROM {_quote(engine, table.name)} "
                    f"WHERE {_quote(engine, column.name)} IS NOT NULL LIMIT {_MAX_VALUE_CARDINALITY + 1}"
                )
                try:
                    with engine.connect() as connection:
                        values = connection.execute(text(query)).scalars().all()
                except Exception:  # noqa: BLE001 - profiling is optional context
                    continue
                usable = [value for value in values if isinstance(value, str) and 0 < len(value) <= 60]
                if 1 < len(usable) <= _MAX_VALUE_CARDINALITY and len(usable) == len(values):
                    table.values[column.name] = usable

    # ------------------------------------------------------------------ output
    def table(self, name: str) -> TableInfo | None:
        return next((table for table in self.tables if table.name == name), None)

    def full_ddl(self) -> str:
        return "\n\n".join(table.to_ddl() for table in self.tables)

    def describe(self, names: list[str]) -> str:
        selected = [table for table in self.tables if table.name in names]
        if not selected:
            return self.full_ddl()
        blocks = [table.to_ddl() for table in selected]
        relations = [
            f"-- {column.name} in {table.name} -> {column.foreign_key}"
            for table in selected
            for column in table.columns
            if column.foreign_key
        ]
        domains = [domain for domain in (table.value_domain() for table in selected) if domain]
        return "\n\n".join(blocks + relations + domains)

    def value_index(self) -> dict[str, list[str]]:
        """Profiled values by qualified column name, for literal matching."""
        return {
            f"{table.name}.{column}": values
            for table in self.tables
            for column, values in table.values.items()
        }

    def as_api_payload(self) -> list[dict]:
        return [
            {
                "table": table.name,
                "row_count": table.row_count,
                "columns": [
                    {
                        "name": column.name,
                        "type": column.data_type,
                        "primary_key": column.primary_key,
                        "foreign_key": column.foreign_key,
                        "nullable": column.nullable,
                    }
                    for column in table.columns
                ],
            }
            for table in self.tables
        ]

    # ---------------------------------------------------------------- retrieval
    def select_relevant_tables(self, question: str, limit: int = 3) -> list[str]:
        tokens = _tokenize(question)
        scores: dict[str, float] = {}
        for table in self.tables:
            score = 0.0
            table_tokens = _tokenize(table.name)
            score += 3.0 * len(tokens & table_tokens)
            for column in table.columns:
                score += 1.5 * len(tokens & _tokenize(column.name))
                score += 2.0 * len(tokens & _tokenize(f"{table.name}_{column.name}"))
            # A JOIN needs both sides of a foreign key to stay in context.
            if any(column.foreign_key for column in table.columns):
                score += 0.5
            scores[table.name] = score

        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        picked = [name for name, score in ranked if score > 0][:limit]
        return picked or [table.name for table in self.tables[:limit]]


def _tokenize(value: str) -> set[str]:
    words = re.split(r"[^a-z0-9]+", value.lower())
    pieces: set[str] = set()
    for word in words:
        if not word or word in _STOPWORDS:
            continue
        pieces.add(word)
        pieces.update(part for part in word.split("_") if len(part) > 2)
    return pieces
