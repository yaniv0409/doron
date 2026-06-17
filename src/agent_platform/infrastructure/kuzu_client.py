from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_platform.domain.exceptions import DatabaseError

try:
    import kuzu
except ImportError:  # pragma: no cover
    kuzu = None


class KuzuGateway:
    def __init__(self, db_path: str, *, read_only: bool = False) -> None:
        if kuzu is None:
            raise DatabaseError("kuzu is not installed")
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._db = kuzu.Database(str(path), read_only=read_only)
        except Exception as exc:  # pragma: no cover
            raise DatabaseError(str(exc)) from exc
        self._connection = kuzu.Connection(self._db)

    def execute(self, query: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        params = parameters or {}
        try:
            result = self._connection.execute(query, parameters=params)
        except Exception as exc:  # pragma: no cover
            raise DatabaseError(str(exc)) from exc
        return _normalize_result(result)

    def get_schema(self) -> str:
        statements = self.show_tables()
        return "\n".join(str(item) for item in statements)

    def show_tables(self) -> list[dict[str, Any]]:
        return self.execute("CALL show_tables() RETURN *;")

    def table_info(self, table_name: str) -> list[dict[str, Any]]:
        return self.execute(f"CALL table_info('{table_name}') RETURN *;")

    def show_connection(self, table_name: str) -> list[dict[str, Any]]:
        return self.execute(f"CALL show_connection('{table_name}') RETURN *;")

    def count_rows(self, table_name: str, *, kind: str) -> int:
        if kind == "REL":
            query = f"MATCH ()-[r:{table_name}]->() RETURN count(*) AS count;"
        else:
            query = f"MATCH (n:{table_name}) RETURN count(*) AS count;"
        rows = self.execute(query)
        if not rows:
            return 0
        count = rows[0].get("count", 0)
        return int(count)

    def sample_rows(self, table_name: str, *, kind: str, limit: int) -> list[dict[str, Any]]:
        if kind == "REL":
            query = f"MATCH (a)-[r:{table_name}]->(b) RETURN a, r, b LIMIT {limit};"
        else:
            query = f"MATCH (n:{table_name}) RETURN n LIMIT {limit};"
        return self.execute(query)


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
