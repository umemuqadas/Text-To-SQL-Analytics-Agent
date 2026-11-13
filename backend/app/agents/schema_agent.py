"""Schema Agent — decides what the rest of the pipeline is allowed to know.

Instead of pasting the entire database into every prompt, it returns only the
tables that match the question, plus any literal values (city names, order
statuses) that appear in it. That keeps prompts small and lets follow-up
questions such as "only from Karachi" resolve to a real filter value — on an
uploaded schema nobody had to annotate in advance.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.config import Settings, get_settings
from app.database.schema import SchemaCatalog

_catalog_cache: dict[tuple[str, ...], SchemaCatalog] = {}


def get_catalog(settings: Settings | None = None, refresh: bool = False) -> SchemaCatalog:
    settings = settings or get_settings()
    key = tuple(settings.allowed_tables)
    if refresh or key not in _catalog_cache:
        _catalog_cache[key] = SchemaCatalog(settings).load(refresh=True)
    return _catalog_cache[key]


def clear_cache() -> None:
    """Drop the catalog so the next question reflects a different datasource."""
    _catalog_cache.clear()


@dataclass(slots=True)
class SchemaContext:
    question: str
    tables: list[str]
    ddl: str
    matched_values: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_prompt_block(self) -> str:
        lines = [f"-- Relevant tables for this question: {', '.join(self.tables)}", self.ddl]
        if self.matched_values:
            pairs = ", ".join(f"{column} = {value!r}" for column, value in self.matched_values.items())
            lines.append(f"-- Values mentioned in the question: {pairs}")
        return "\n".join(lines)


def retrieve_schema(question: str, settings: Settings | None = None) -> SchemaContext:
    settings = settings or get_settings()
    catalog = get_catalog(settings)
    tables = catalog.select_relevant_tables(question)
    return SchemaContext(
        question=question,
        tables=tables,
        ddl=catalog.describe(tables),
        matched_values=_match_values(question, catalog),
    )


def _match_values(question: str, catalog: SchemaCatalog) -> dict[str, str]:
    lowered = question.lower()
    found: dict[str, str] = {}
    for qualified, values in catalog.value_index().items():
        # Longest first so "San Francisco" beats a hypothetical "San".
        for value in sorted(values, key=len, reverse=True):
            if _mentions(lowered, value):
                found[qualified] = value
                break
    return found


def _mentions(lowered: str, value: str) -> bool:
    """Whole-word match, so the status ``yes`` is not found inside ``yesterday``."""
    return re.search(rf"(?<!\w){re.escape(value.lower())}(?!\w)", lowered) is not None
