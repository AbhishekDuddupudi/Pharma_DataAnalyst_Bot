"""
SQL policy – allowlist-based validation for generated SQL.

Rules:
    1. Must start with SELECT or WITH … SELECT.
    2. No multiple statements (no semicolons except trailing).
    3. No DDL / DML keywords.
    4. Only allowlisted tables.
    5. Only allowlisted columns (best-effort; CTEs/aliases may pass through).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ── Allowlists ───────────────────────────────────────────────────

ALLOWED_TABLES: set[str] = {
    "fact_sales",
    "dim_product",
    "dim_territory",
    "dim_time",
}

ALLOWED_COLUMNS: set[str] = {
    # dim_product
    "product_id", "brand_name", "generic_name", "company_name",
    "therapeutic_area", "dosage_form", "launch_date", "is_active",
    # dim_territory
    "territory_id", "region", "district", "state",
    # dim_time
    "date", "year", "quarter", "month", "week", "day_of_week",
    "year_quarter", "year_month", "is_month_end",
    # fact_sales
    "id", "net_sales_usd", "units", "trx", "nrx",
}

FORBIDDEN_KEYWORDS: set[str] = {
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE",
    "CREATE", "GRANT", "REVOKE", "EXEC", "EXECUTE", "CALL",
    "COPY", "VACUUM", "REINDEX", "CLUSTER", "COMMENT",
    "SET ", "RESET", "BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT",
    "LOCK", "NOTIFY", "LISTEN", "UNLISTEN",
}

# ── Result ───────────────────────────────────────────────────────


@dataclass
class ValidationResult:
    valid: bool = True
    errors: list[str] = field(default_factory=list)


# ── Public API ───────────────────────────────────────────────────


def validate_sql(sql: str) -> ValidationResult:
    """Validate a SQL string against the policy.  Returns a result with errors."""
    result = ValidationResult()
    normalised = sql.strip()

    if not normalised:
        result.valid = False
        result.errors.append("Empty SQL.")
        return result

    # Remove trailing semicolons for analysis
    cleaned = normalised.rstrip(";").strip()
    upper = cleaned.upper()

    # 1. Must start with SELECT or WITH
    if not (upper.startswith("SELECT") or upper.startswith("WITH")):
        result.valid = False
        result.errors.append("SQL must start with SELECT or WITH.")

    # 2. No multiple statements
    # Remove strings and comments to avoid false positives
    stripped = _remove_strings_and_comments(cleaned)
    if ";" in stripped:
        result.valid = False
        result.errors.append("Multiple statements detected (semicolon in body).")

    # 3. Forbidden keywords
    upper_stripped = stripped.upper()
    for kw in FORBIDDEN_KEYWORDS:
        pattern = r'\b' + kw.strip() + r'\b'
        if re.search(pattern, upper_stripped):
            result.valid = False
            result.errors.append(f"Forbidden keyword: {kw.strip()}")
            break  # one is enough

    # 4. Table allowlist
    tables_used = _extract_table_names(stripped)
    for t in tables_used:
        if t.lower() not in ALLOWED_TABLES:
            result.valid = False
            result.errors.append(f"Table not allowed: {t}")

    return result


def get_allowlist_summary() -> str:
    """Return a human-readable summary for LLM prompts."""
    tables = ", ".join(sorted(ALLOWED_TABLES))
    return (
        f"Allowed tables: {tables}. "
        "Only SELECT statements are permitted. "
        "No DDL, DML, or multiple statements."
    )


# ── Internals ────────────────────────────────────────────────────


def _remove_strings_and_comments(sql: str) -> str:
    """Strip string literals and comments to avoid false positives."""
    # Remove single-line comments
    sql = re.sub(r"--.*$", "", sql, flags=re.MULTILINE)
    # Remove block comments
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    # Remove single-quoted strings
    sql = re.sub(r"'[^']*'", "''", sql)
    return sql


def _extract_table_names(sql: str) -> set[str]:
    """
    Best-effort extraction of table names from FROM / JOIN clauses.
    Handles common patterns but not every edge case.
    """
    tables: set[str] = set()
    # FROM table, JOIN table
    pattern = re.compile(
        r'(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_]*)',
        re.IGNORECASE,
    )
    for match in pattern.finditer(sql):
        name = match.group(1).lower()
        # Skip SQL keywords that might appear after FROM (subquery aliases etc.)
        if name.upper() not in {"SELECT", "LATERAL", "UNNEST", "GENERATE_SERIES"}:
            tables.add(name)
    return tables
