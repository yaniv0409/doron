from __future__ import annotations

from agent_platform.application import db_snapshot_service as snapshot_service
from agent_platform.contracts.db import DbContentsRequest


class FakeGateway:
    def __init__(self, db_path: str, read_only: bool = False) -> None:
        self.db_path = db_path
        self.read_only = read_only

    def show_tables(self) -> list[dict[str, object]]:
        return [
            {"name": "Company", "type": "NODE"},
            {"name": "WorksWith", "type": "REL"},
        ]

    def table_info(self, table_name: str) -> list[dict[str, object]]:
        return [{"name": f"{table_name}.name", "type": "STRING"}]

    def show_connection(self, table_name: str) -> list[dict[str, object]]:
        return [{"source table name": "Company", "destination table name": "Company"}]

    def count_rows(self, table_name: str, *, kind: str) -> int:
        return 2 if kind == "NODE" else 1

    def sample_rows(self, table_name: str, *, kind: str, limit: int) -> list[dict[str, object]]:
        return [{"name": table_name, "kind": kind, "limit": limit}]


def test_db_snapshot_service_builds_structured_snapshot(monkeypatch) -> None:
    monkeypatch.setattr(snapshot_service, "KuzuGateway", FakeGateway)
    service = snapshot_service.DbSnapshotService()

    response = service.build_snapshot(
        DbContentsRequest(
            db_path="/tmp/demo.kuzu",
            sample_limit=3,
            include_schema=True,
            include_counts=True,
            include_connections=True,
        )
    )

    assert response.db_path == "/tmp/demo.kuzu"
    assert response.table_count == 2
    assert response.tables[0].table_schema == [{"name": "Company.name", "type": "STRING"}]
    assert response.tables[0].sample_rows == [{"name": "Company", "kind": "NODE", "limit": 3}]
    assert response.tables[0].row_count == 2
    assert response.tables[1].connection == {
        "source table name": "Company",
        "destination table name": "Company",
    }


def test_db_snapshot_service_can_skip_relations(monkeypatch) -> None:
    monkeypatch.setattr(snapshot_service, "KuzuGateway", FakeGateway)
    service = snapshot_service.DbSnapshotService()

    response = service.build_snapshot(
        DbContentsRequest(
            db_path="/tmp/demo.kuzu",
            sample_limit=2,
            include_schema=False,
            include_counts=False,
            include_connections=False,
        )
    )

    assert len(response.tables) == 2
    assert response.tables[0].table_schema == []
    assert response.tables[0].row_count is None
