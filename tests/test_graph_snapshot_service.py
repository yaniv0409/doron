from __future__ import annotations

from agent_platform.application import graph_snapshot_service as snapshot_service


class FakeGateway:
    last_instance: "FakeGateway | None" = None

    def __init__(self, db_path: str, read_only: bool = False) -> None:
        self.db_path = db_path
        self.read_only = read_only
        self.queries: list[str] = []
        FakeGateway.last_instance = self

    def show_tables(self) -> list[dict[str, object]]:
        return [
            {"name": "Company", "type": "NODE"},
            {"name": "Person", "type": "NODE"},
            {"name": "WorksWith", "type": "REL"},
        ]

    def sample_rows(self, table_name: str, *, kind: str, limit: int) -> list[dict[str, object]]:
        if kind == "REL":
            query = f"MATCH (a)-[r:`{table_name}`]->(b) RETURN a, r, b LIMIT {limit};"
        else:
            query = f"MATCH (n:`{table_name}`) RETURN n LIMIT {limit};"
        self.queries.append(query)
        if query.startswith("MATCH (n:`Company`)"):
            return [
                {"n": {"_id": "company:1", "_label": "Company", "_properties": {"name": "Acme"}}},
                {"n": {"_id": "company:2", "_label": "Company", "_properties": {"name": "Globex"}}},
            ]
        if query.startswith("MATCH (n:`Person`)"):
            return [
                {"n": {"_id": "person:1", "_label": "Person", "_properties": {"name": "Ada"}}},
                {"n": {"_id": "person:2", "_label": "Person", "_properties": {"name": "Linus"}}},
            ]
        if query.startswith("MATCH (a)-[r:`WorksWith`]"):
            return [
                {
                    "a": {"_id": "person:1", "_label": "Person", "_properties": {"name": "Ada"}},
                    "r": {"_id": "rel:1", "_label": "WorksWith", "_properties": {"since": 2024}},
                    "b": {"_id": "company:1", "_label": "Company", "_properties": {"name": "Acme"}},
                },
                {
                    "a": {"_id": "person:2", "_label": "Person", "_properties": {"name": "Linus"}},
                    "r": {"_id": "rel:2", "_label": "WorksWith", "_properties": {"since": 2025}},
                    "b": {"_id": "company:1", "_label": "Company", "_properties": {"name": "Acme"}},
                },
            ]
        raise AssertionError(f"unexpected query: {query}")


def test_graph_snapshot_service_scans_all_tables(monkeypatch) -> None:
    monkeypatch.setattr(snapshot_service, "KuzuGateway", FakeGateway)
    service = snapshot_service.GraphSnapshotService(settings=type("Settings", (), {"graph_node_limit": 10, "graph_edge_limit": 10})())

    response = service.build_snapshot("session-1", "memory", "/tmp/demo.kuzu")

    assert response.node_count == 4
    assert response.edge_count == 2
    assert response.node_limit == 10
    assert response.edge_limit == 10
    assert response.is_truncated is False
    assert {node.id for node in response.nodes} == {
        "company:1",
        "company:2",
        "person:1",
        "person:2",
    }
    assert {edge.id for edge in response.edges} == {"rel:1", "rel:2"}
    assert FakeGateway.last_instance is not None
    assert FakeGateway.last_instance.queries == [
        "MATCH (n:`Company`) RETURN n LIMIT 10;",
        "MATCH (n:`Person`) RETURN n LIMIT 8;",
        "MATCH (a)-[r:`WorksWith`]->(b) RETURN a, r, b LIMIT 10;",
    ]


def test_graph_snapshot_service_respects_limits(monkeypatch) -> None:
    monkeypatch.setattr(snapshot_service, "KuzuGateway", FakeGateway)
    service = snapshot_service.GraphSnapshotService(settings=type("Settings", (), {"graph_node_limit": 3, "graph_edge_limit": 1})())

    response = service.build_snapshot("session-1", "memory", "/tmp/demo.kuzu")

    assert response.node_count == 3
    assert response.edge_count == 1
    assert response.is_truncated is True
    assert response.node_limit == 3
    assert response.edge_limit == 1
    assert FakeGateway.last_instance is not None
    assert FakeGateway.last_instance.queries == [
        "MATCH (n:`Company`) RETURN n LIMIT 3;",
        "MATCH (n:`Person`) RETURN n LIMIT 1;",
        "MATCH (a)-[r:`WorksWith`]->(b) RETURN a, r, b LIMIT 1;",
    ]
