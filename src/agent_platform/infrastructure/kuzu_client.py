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
        self._db_path = str(Path(db_path))
        self._read_only = read_only
        self._db: Any | None = None
        self._connection: Any | None = None
        self._ensure_open()

    def execute(self, query: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        self._ensure_open()
        params = parameters or {}
        try:
            result = self._connection.execute(query, parameters=params)
        except Exception as exc:  # pragma: no cover
            raise DatabaseError(str(exc)) from exc
        return _normalize_result(result)

    def sync(self) -> None:
        self.close()
        self._ensure_open()

    def close(self) -> None:
        connection = self._connection
        db = self._db
        self._connection = None
        self._db = None
        errors: list[Exception] = []
        if connection is not None:
            try:
                connection.close()
            except Exception as exc:  # pragma: no cover
                errors.append(exc)
        if db is not None:
            try:
                db.close()
            except Exception as exc:  # pragma: no cover
                errors.append(exc)
        if errors:
            raise DatabaseError(str(errors[0]))

    def get_schema(self) -> str:
        statements = self.show_tables()
        return "\n".join(str(item) for item in statements)

    def show_tables(self) -> list[dict[str, Any]]:
        return self.execute("CALL show_tables() RETURN *;")

    def table_names(self) -> list[str]:
        names: list[str] = []
        for row in self.show_tables():
            for key in ("name", "table name", "table_name"):
                value = row.get(key)
                if isinstance(value, str):
                    names.append(value)
                    break
        return names

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
            query = f"MATCH (a)-[r:{_quote_identifier(table_name)}]->(b) RETURN a, r, b LIMIT {limit};"
        else:
            query = f"MATCH (n:{_quote_identifier(table_name)}) RETURN n LIMIT {limit};"
        return self.execute(query)

    def _ensure_open(self) -> None:
        if self._db is not None and self._connection is not None:
            return
        if kuzu is None:
            raise DatabaseError("kuzu is not installed")
        path = Path(self._db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._db = kuzu.Database(str(path), read_only=self._read_only)
            self._connection = kuzu.Connection(self._db)
        except Exception as exc:  # pragma: no cover
            self._db = None
            self._connection = None
            raise DatabaseError(str(exc)) from exc


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


def _quote_identifier(name: str) -> str:
    return f"`{name.replace('`', '``')}`"
