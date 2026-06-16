from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_platform.domain.exceptions import DatabaseError

try:
    import kuzu
except ImportError:  # pragma: no cover
    kuzu = None


class KuzuGateway:
    def __init__(self, db_path: str) -> None:
        if kuzu is None:
            raise DatabaseError("kuzu is not installed")
        path = Path(db_path)
        if not path.exists():
            raise DatabaseError(f"database file not found: {db_path}")
        self._db = kuzu.Database(str(path))
        self._connection = kuzu.Connection(self._db)

    def execute(self, query: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        params = parameters or {}
        try:
            result = self._connection.execute(query, parameters=params)
        except Exception as exc:  # pragma: no cover
            raise DatabaseError(str(exc)) from exc
        return _normalize_result(result)

    def get_schema(self) -> str:
        statements = self.execute("CALL SHOW_TABLES() RETURN *;")
        return "\n".join(str(item) for item in statements)


def _normalize_result(result: Any) -> list[dict[str, Any]]:
    if result is None:
        return []
    rows: list[dict[str, Any]] = []
    try:
        column_names = list(result.get_column_names())
    except Exception:
        return []
    while result.has_next():
        row = result.get_next()
        rows.append({column_names[index]: value for index, value in enumerate(row)})
    return rows
