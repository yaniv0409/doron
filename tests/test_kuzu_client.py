from pathlib import Path

import agent_platform.infrastructure.kuzu_client as kuzu_client


class FakeDatabase:
    init_count = 0
    close_count = 0

    def __init__(self, path: str, read_only: bool = False, **_: object) -> None:
        self.path = path
        self.read_only = read_only
        self.closed = False
        FakeDatabase.init_count += 1

    def close(self) -> None:
        self.closed = True
        FakeDatabase.close_count += 1


class FakeConnection:
    def __init__(self, database: FakeDatabase) -> None:
        self.database = database
        self.closed = False

    def execute(self, query: str, parameters=None):
        return FakeResult([{"query": query}])

    def close(self) -> None:
        self.closed = True


class FakeResult:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.index = 0

    def get_column_names(self):
        if not self.rows:
            return []
        return list(self.rows[0].keys())

    def has_next(self):
        return self.index < len(self.rows)

    def get_next(self):
        row = self.rows[self.index]
        self.index += 1
        return list(row.values())


class FakeKuzuModule:
    Database = FakeDatabase
    Connection = FakeConnection


def test_kuzu_gateway_creates_parent_directory_for_missing_path(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "nested" / "demo.kuzu"
    monkeypatch.setattr(kuzu_client, "kuzu", FakeKuzuModule)

    gateway = kuzu_client.KuzuGateway(str(target))

    assert target.parent.exists()
    assert gateway._db.path == str(target)


def test_kuzu_gateway_can_open_read_only(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "demo.kuzu"
    monkeypatch.setattr(kuzu_client, "kuzu", FakeKuzuModule)

    gateway = kuzu_client.KuzuGateway(str(target), read_only=True)

    assert gateway._db.read_only is True


def test_kuzu_gateway_sync_reopens_after_close(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "demo.kuzu"
    FakeDatabase.init_count = 0
    FakeDatabase.close_count = 0
    monkeypatch.setattr(kuzu_client, "kuzu", FakeKuzuModule)

    gateway = kuzu_client.KuzuGateway(str(target))
    first = gateway.execute("RETURN 1 AS query;")
    gateway.sync()
    second = gateway.execute("RETURN 2 AS query;")

    assert first == [{"query": "RETURN 1 AS query;"}]
    assert second == [{"query": "RETURN 2 AS query;"}]
    assert FakeDatabase.init_count == 2
    assert FakeDatabase.close_count == 1
