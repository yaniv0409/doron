from __future__ import annotations

import asyncio
import json

from agent_platform.application.memory_manager import MemoryManager
from agent_platform.config.settings import MemorySettings


class FakeEmbeddingClient:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(texts[0]))]]


class FakeDb:
    def __init__(self) -> None:
        self.tables: set[str] = set()
        self.records: dict[str, dict[str, object]] = {}

    def table_names(self) -> list[str]:
        return list(self.tables)

    def execute(self, query: str, parameters: dict[str, object] | None = None) -> list[dict[str, object]]:
        params = parameters or {}
        if "CREATE NODE TABLE MemoryEntry" in query:
            self.tables.add("MemoryEntry")
            return []
        if "CREATE (m:MemoryEntry" in query:
            self.records[str(params["id"])] = dict(params)
            return []
        if "MATCH (m:MemoryEntry)" in query and "SET m.kind" in query:
            current = self.records[str(params["id"])]
            current.update(params)
            return [{"id": params["id"]}]
        if "MATCH (m:MemoryEntry)" in query and "SET m.status = 'deprecated'" in query:
            current = self.records[str(params["id"])]
            current["status"] = "deprecated"
            current["provenance_json"] = params["provenance_json"]
            current["updated_at"] = params["updated_at"]
            return [{"id": params["id"]}]
        if "MATCH (m:MemoryEntry)" in query and "RETURN" in query:
            rows: list[dict[str, object]] = []
            for item in self.records.values():
                rows.append(
                    {
                        "id": item["id"],
                        "kind": item["kind"],
                        "title": item["title"],
                        "body": item["body"],
                        "tags_json": item["tags_json"],
                        "confidence": item["confidence"],
                        "status": item["status"],
                        "provenance_json": item["provenance_json"],
                        "created_at": item["created_at"],
                        "updated_at": item["updated_at"],
                        "last_used_at": item["last_used_at"],
                        "embedding_json": item["embedding_json"],
                    }
                )
            return rows
        raise AssertionError(f"unexpected query: {query}")


def test_memory_manager_searches_and_mutates_records() -> None:
    async def scenario() -> None:
        manager = MemoryManager(MemorySettings(), FakeEmbeddingClient())
        db = FakeDb()

        writes = await manager.write_entries(
            db,
            [
                {
                    "id": "skill-1",
                    "kind": "skill",
                    "title": "Company research preflight",
                    "body": "Check source packs before browsing.",
                    "tags": ["company", "sources"],
                    "provenance": {"trace_id": "trace-1"},
                }
            ],
            reason="save skill",
        )
        assert writes[0].memory_id == "skill-1"

        results = await manager.search(db, "company source packs", kinds=["skill"], limit=5, source="test")
        assert results[0].title == "Company research preflight"

        updates = await manager.update_entries(
            db,
            [
                {
                    "id": "skill-1",
                    "body": "Check learned source packs before browsing.",
                }
            ],
            reason="tighten skill",
        )
        assert updates[0].action == "update"
        assert manager.read(db, ["skill-1"])[0].body == "Check learned source packs before browsing."

        deprecations = manager.deprecate_entries(db, ["skill-1"], reason="obsolete")
        assert deprecations[0].status == "deprecated"
        assert manager.read(db, ["skill-1"])[0].status == "deprecated"

    asyncio.run(scenario())


def test_memory_manager_ensure_schema_creates_table_once() -> None:
    db = FakeDb()
    manager = MemoryManager(MemorySettings(), FakeEmbeddingClient())

    manager.ensure_schema(db)
    manager.ensure_schema(db)

    assert db.table_names() == ["MemoryEntry"]
