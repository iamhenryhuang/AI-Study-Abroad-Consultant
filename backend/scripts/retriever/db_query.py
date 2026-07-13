"""Shared helpers for read-only retriever database queries."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Mapping

from db.connection import get_connection


@contextmanager
def readonly_connection() -> Iterator[Any | None]:
    """Yield a read-only connection and always close it after use.

    ``None`` is yielded when the connection factory cannot provide a
    connection, allowing each caller to retain its existing error message.
    """
    conn = get_connection()
    if not conn:
        yield None
        return

    try:
        conn.read_only = True
        yield conn
    finally:
        conn.close()


def fetch_dicts(
    conn: Any,
    sql: str,
    params: Mapping[str, Any] | None = None,
) -> list[dict]:
    """Execute a query and map every result row to a column-keyed dict."""
    with conn.cursor() as cur:
        if params is None:
            cur.execute(sql)
        else:
            cur.execute(sql, params)
        columns = [desc[0] for desc in cur.description] if cur.description else []
        rows = cur.fetchall()
    return [dict(zip(columns, row)) for row in rows]
