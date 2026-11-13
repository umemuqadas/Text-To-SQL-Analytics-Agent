"""Static SQL safety checks applied to every LLM-generated query.

Nothing reaches the database without passing here first. The rules are
defence-in-depth on top of a read-only DB role:

* exactly one statement, and it must be a SELECT (or a read-only query)
* only tables the deployment allow-lists
* hidden columns may not be selected, filtered on, or covered by `SELECT *`
* no file/system/catalog functions
* a row cap is always applied
"""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

from app.config import Settings, get_settings

# Statement kinds that are never acceptable, whatever the parser calls them.
FORBIDDEN_EXPRESSIONS: tuple[type[exp.Expression], ...] = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Drop, exp.Create, exp.Alter,
    exp.TruncateTable, exp.Copy, exp.Grant, exp.Transaction, exp.Command, exp.Set,
    exp.Use, exp.Analyze,
    # SQLite-specific escapes: ATTACH can pull in another file on disk.
    exp.Attach, exp.Detach, exp.Pragma,
)

# Functions that escape the data: filesystem, sleeps, catalogs, network.
FORBIDDEN_FUNCTIONS = {
    "pg_sleep", "pg_advisory_lock", "pg_terminate_backend", "pg_cancel_backend",
    "pg_ls_dir", "pg_read_file", "pg_read_binary_file", "pg_catalog", "current_setting",
    "lo_import", "lo_export", "dblink", "dblink_exec", "load_file", "readfile",
    "writefile", "system", "exec", "load_extension",
}

_READ_ONLY_QUERIES: tuple[type[exp.Expression], ...] = (
    exp.Select, exp.Union, exp.Except, exp.Intersect,
)


@dataclass(slots=True)
class ValidationReport:
    ok: bool
    sql: str = ""
    statement_type: str = ""
    tables: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "sql": self.sql,
            "statement_type": self.statement_type,
            "tables": self.tables,
            "errors": self.errors,
            "warnings": self.warnings,
        }


def _statement_label(statement: exp.Expression) -> str:
    """Human-readable verb for an AST node (`DROP`, `UPDATE`, …)."""
    return (getattr(statement, "key", "") or type(statement).__name__).upper()


def validate_sql(
    sql: str,
    settings: Settings | None = None,
) -> ValidationReport:
    settings = settings or get_settings()
    dialect = "postgres" if not settings.is_sqlite else "sqlite"
    allowed = {name.lower() for name in settings.allowed_tables}
    errors: list[str] = []
    warnings: list[str] = []

    raw = (sql or "").strip().rstrip(";").strip()
    if not raw:
        return ValidationReport(ok=False, errors=["No SQL was produced for this question."])

    try:
        statements = [statement for statement in sqlglot.parse(raw, read=dialect) if statement is not None]
    except sqlglot.errors.ParseError as exc:
        return ValidationReport(ok=False, errors=[f"SQL could not be parsed: {exc}"])

    if not statements:
        return ValidationReport(ok=False, errors=["No SQL statement found."])
    if len(statements) > 1:
        return ValidationReport(
            ok=False, errors=[f"Only one statement is allowed; received {len(statements)}."]
        )

    statement = statements[0]

    blocked = [kind for kind in FORBIDDEN_EXPRESSIONS if isinstance(statement, kind)]
    if blocked:
        label = _statement_label(statement)
        return ValidationReport(
            ok=False,
            statement_type=label,
            errors=[f"{label} is not allowed. The agent has read-only access."],
        )
    if not isinstance(statement, _READ_ONLY_QUERIES):
        label = _statement_label(statement)
        return ValidationReport(
            ok=False,
            statement_type=label,
            errors=[f"Only SELECT queries are allowed; received {label}."],
        )

    # CTE names are local aliases, not real tables. Comparison is case-folded:
    # models write `WITH CategoryRevenue AS (…)` and SQL resolves it case-
    # insensitively, so the alias must not look like an unauthorised table.
    local_names = {cte.alias.lower() for cte in statement.find_all(exp.CTE) if cte.alias}
    tables = sorted({
        table.name.lower()
        for table in statement.find_all(exp.Table)
        if table.name and table.name.lower() not in local_names
    })
    if not tables:
        errors.append("The query does not reference any known table.")
    unauthorised = sorted(set(tables) - allowed)
    if unauthorised:
        errors.append(f"Access to table(s) {', '.join(unauthorised)} is not permitted.")

    for call in statement.find_all(exp.Anonymous):
        if call.name.lower() in FORBIDDEN_FUNCTIONS:
            errors.append(f"Function {call.name}() is blocked.")

    hidden_pairs = [
        (column.split(".", 1)[0].lower(), column.split(".", 1)[1].lower())
        for column in settings.hidden_columns
        if "." in column
    ]
    if hidden_pairs:
        # `FROM customers c` means `c.email` refers to the protected column, so
        # qualifiers are resolved through the query's own table aliases.
        aliases = {
            (table.alias or table.name).lower(): table.name.lower()
            for table in statement.find_all(exp.Table)
            if table.name
        }
        for column in statement.find_all(exp.Column):
            name = column.name.lower()
            qualifier = (column.table or "").lower()
            if qualifier:
                candidates = {aliases.get(qualifier, qualifier)}
            else:
                candidates = set(tables)
            for hidden_table, hidden_column in hidden_pairs:
                if name == hidden_column and hidden_table in candidates:
                    errors.append(f"Column {hidden_table}.{hidden_column} is not accessible.")

        # `SELECT *` would silently expose hidden columns. COUNT(*) is not a
        # projection star, so ignore stars inside functions.
        for star in statement.find_all(exp.Star):
            if star.find_ancestor(exp.Func):
                continue
            covered = {hidden_table for hidden_table, _ in hidden_pairs if hidden_table in tables}
            if covered:
                errors.append(
                    f"SELECT * is not allowed here because {', '.join(sorted(covered))} has protected columns; "
                    "list the columns explicitly."
                )

    clamped = _apply_row_limit(statement, settings.max_rows, warnings)

    return ValidationReport(
        ok=not errors,
        sql=clamped.sql(dialect=dialect),
        statement_type="SELECT",
        tables=tables,
        errors=errors,
        warnings=warnings,
    )


def _apply_row_limit(statement: exp.Expression, max_rows: int, warnings: list[str]) -> exp.Expression:
    limit_node = statement.args.get("limit")
    if limit_node is None:
        warnings.append(f"Added LIMIT {max_rows} to cap the result set.")
        return statement.limit(max_rows)
    try:
        current = int(limit_node.expression.sql())
    except (TypeError, ValueError):
        return statement
    if current > max_rows:
        warnings.append(f"Reduced LIMIT {current} to the maximum of {max_rows} rows.")
        return statement.limit(max_rows, copy=False)
    return statement
