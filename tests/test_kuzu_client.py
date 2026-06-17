from pathlib import Path

import agent_platform.infrastructure.kuzu_client as kuzu_client


class FakeDatabase:
    def __init__(self, path: str, read_only: bool = False, **_: object) -> None:
        self.path = path
        self.read_only = read_only


class FakeConnection:
    def __init__(self, database: FakeDatabase) -> None:
        self.database = database


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
