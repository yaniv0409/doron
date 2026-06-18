from __future__ import annotations

import json
import math
import uuid
from datetime import datetime
from typing import Any

from agent_platform.config.settings import MemorySettings
from agent_platform.domain.exceptions import DatabaseError
from agent_platform.domain.models import DurableMemoryRecord, MemoryMutationRecord, MemoryRetrievalRecord, utc_now
from agent_platform.infrastructure.kuzu_client import KuzuGateway
from agent_platform.infrastructure.openrouter_client import OpenRouterEmbeddingClient


class MemoryManager:
    def __init__(
        self,
        settings: MemorySettings,
        embedding_client: OpenRouterEmbeddingClient,
    ) -> None:
        self._settings = settings
        self._embedding_client = embedding_client

    async def preflight(self, runtime: Any) -> None:
        if not self._settings.enabled:
            return
        results = await self.search(
            runtime.db,
            runtime.context.mission_request.prompt,
            kinds=None,
            limit=self._settings.preflight_limit,
            source="preflight",
        )
        runtime.context.memory_retrievals.append(
            MemoryRetrievalRecord(query=runtime.context.mission_request.prompt, kinds=[], results=results, source="preflight")
        )
        runtime.context.retrieved_skills = [item for item in results if item.kind == "skill"]
        runtime.context.retrieved_source_packs = [item for item in results if item.kind == "source_pack"]
        runtime.context.durable_memories = [
            item for item in results if item.kind not in {"skill", "source_pack"}
        ]

    async def search(
        self,
        db: KuzuGateway,
        query: str,
        *,
        kinds: list[str] | None = None,
        limit: int,
        source: str,
    ) -> list[DurableMemoryRecord]:
        records = self._fetch_records(db)
        active = [item for item in records if item.status != "deprecated"]
        if kinds is not None:
            allowed = set(kinds)
            active = [item for item in active if item.kind in allowed]
        if not active or not query.strip():
            return active[:limit]
        embedding = await self._embed_query(query)
        ranked = sorted(
            active,
            key=lambda item: self._score(query, item, embedding),
            reverse=True,
        )
        results = [item.model_copy(update={"score": self._score(query, item, embedding)}) for item in ranked[:limit]]
        return results

    async def related_for_maintenance(self, db: KuzuGateway, trace_text: str) -> list[DurableMemoryRecord]:
        return await self.search(
            db,
            trace_text,
            kinds=None,
            limit=self._settings.maintenance_related_memory_limit,
            source="maintenance_related",
        )

    async def write_entries(
        self,
        db: KuzuGateway,
        entries: list[dict[str, Any]],
        *,
        reason: str,
    ) -> list[MemoryMutationRecord]:
        self.ensure_schema(db)
        mutations: list[MemoryMutationRecord] = []
        now = utc_now().isoformat()
        for entry in entries:
            memory_id = str(entry.get("id") or uuid.uuid4())
            kind = str(entry.get("kind") or "memory")
            title = str(entry.get("title") or "").strip()
            body = str(entry.get("body") or "").strip()
            if not title or not body:
                continue
            tags = _normalize_tags(entry.get("tags"))
            provenance = entry.get("provenance") if isinstance(entry.get("provenance"), dict) else {}
            confidence = float(entry.get("confidence") or 0.5)
            embedding_json = await self._embed_payload(title, body, tags)
            db.execute(
                """
                CREATE (m:MemoryEntry {
                    id: $id,
                    kind: $kind,
                    title: $title,
                    body: $body,
                    tags_json: $tags_json,
                    confidence: $confidence,
                    status: $status,
                    provenance_json: $provenance_json,
                    embedding_json: $embedding_json,
                    created_at: $created_at,
                    updated_at: $updated_at,
                    last_used_at: $last_used_at
                });
                """,
                {
                    "id": memory_id,
                    "kind": kind,
                    "title": title,
                    "body": body,
                    "tags_json": json.dumps(tags),
                    "confidence": confidence,
                    "status": str(entry.get("status") or "active"),
                    "provenance_json": json.dumps(provenance),
                    "embedding_json": embedding_json,
                    "created_at": now,
                    "updated_at": now,
                    "last_used_at": now,
                },
            )
            mutations.append(
                MemoryMutationRecord(
                    action="write",
                    memory_id=memory_id,
                    kind=kind,
                    title=title,
                    reason=reason,
                    status=str(entry.get("status") or "active"),
                )
            )
        return mutations

    async def update_entries(
        self,
        db: KuzuGateway,
        entries: list[dict[str, Any]],
        *,
        reason: str,
    ) -> list[MemoryMutationRecord]:
        self.ensure_schema(db)
        mutations: list[MemoryMutationRecord] = []
        now = utc_now().isoformat()
        existing = {item.id: item for item in self._fetch_records(db)}
        for entry in entries:
            memory_id = str(entry.get("id") or "")
            current = existing.get(memory_id)
            if current is None:
                continue
            title = str(entry.get("title") or current.title).strip()
            body = str(entry.get("body") or current.body).strip()
            tags = _normalize_tags(entry.get("tags") or current.tags)
            provenance = entry.get("provenance") if isinstance(entry.get("provenance"), dict) else current.provenance
            confidence = float(entry.get("confidence") or current.confidence)
            status = str(entry.get("status") or current.status)
            kind = str(entry.get("kind") or current.kind)
            embedding_json = await self._embed_payload(title, body, tags)
            db.execute(
                """
                MATCH (m:MemoryEntry)
                WHERE m.id = $id
                SET m.kind = $kind,
                    m.title = $title,
                    m.body = $body,
                    m.tags_json = $tags_json,
                    m.confidence = $confidence,
                    m.status = $status,
                    m.provenance_json = $provenance_json,
                    m.embedding_json = $embedding_json,
                    m.updated_at = $updated_at,
                    m.last_used_at = $last_used_at
                RETURN m.id AS id;
                """,
                {
                    "id": memory_id,
                    "kind": kind,
                    "title": title,
                    "body": body,
                    "tags_json": json.dumps(tags),
                    "confidence": confidence,
                    "status": status,
                    "provenance_json": json.dumps(provenance),
                    "embedding_json": embedding_json,
                    "updated_at": now,
                    "last_used_at": now,
                },
            )
            mutations.append(
                MemoryMutationRecord(
                    action="update",
                    memory_id=memory_id,
                    kind=kind,
                    title=title,
                    reason=reason,
                    status=status,
                )
            )
        return mutations

    def deprecate_entries(
        self,
        db: KuzuGateway,
        ids: list[str],
        *,
        reason: str,
        replacement_id: str | None = None,
    ) -> list[MemoryMutationRecord]:
        self.ensure_schema(db)
        mutations: list[MemoryMutationRecord] = []
        now = utc_now().isoformat()
        existing = {item.id: item for item in self._fetch_records(db)}
        for memory_id in ids:
            current = existing.get(memory_id)
            if current is None:
                continue
            provenance = dict(current.provenance)
            provenance["deprecation_reason"] = reason
            if replacement_id:
                provenance["replaced_by"] = replacement_id
            db.execute(
                """
                MATCH (m:MemoryEntry)
                WHERE m.id = $id
                SET m.status = 'deprecated',
                    m.provenance_json = $provenance_json,
                    m.updated_at = $updated_at
                RETURN m.id AS id;
                """,
                {
                    "id": memory_id,
                    "provenance_json": json.dumps(provenance),
                    "updated_at": now,
                },
            )
            mutations.append(
                MemoryMutationRecord(
                    action="deprecate",
                    memory_id=memory_id,
                    kind=current.kind,
                    title=current.title,
                    reason=reason,
                    status="deprecated",
                )
            )
        return mutations

    def read(self, db: KuzuGateway, ids: list[str]) -> list[DurableMemoryRecord]:
        wanted = set(ids)
        return [item for item in self._fetch_records(db) if item.id in wanted]

    def ensure_schema(self, db: KuzuGateway) -> None:
        if not self._settings.enabled:
            return
        try:
            table_names = set(db.table_names())
        except DatabaseError:
            table_names = set()
        if "MemoryEntry" in table_names:
            return
        db.execute(
            """
            CREATE NODE TABLE MemoryEntry(
                id STRING,
                kind STRING,
                title STRING,
                body STRING,
                tags_json STRING,
                confidence DOUBLE,
                status STRING,
                provenance_json STRING,
                embedding_json STRING,
                created_at STRING,
                updated_at STRING,
                last_used_at STRING,
                PRIMARY KEY(id)
            );
            """
        )

    def _fetch_records(self, db: KuzuGateway) -> list[DurableMemoryRecord]:
        try:
            table_names = set(db.table_names())
        except DatabaseError:
            return []
        if "MemoryEntry" not in table_names:
            return []
        rows = db.execute(
            """
            MATCH (m:MemoryEntry)
            RETURN
                m.id AS id,
                m.kind AS kind,
                m.title AS title,
                m.body AS body,
                m.tags_json AS tags_json,
                m.confidence AS confidence,
                m.status AS status,
                m.provenance_json AS provenance_json,
                m.created_at AS created_at,
                m.updated_at AS updated_at,
                m.last_used_at AS last_used_at,
                m.embedding_json AS embedding_json
            LIMIT 500;
            """
        )
        return [_row_to_record(row) for row in rows]

    async def _embed_query(self, query: str) -> list[float] | None:
        if not self._settings.enabled:
            return None
        try:
            vectors = await self._embedding_client.embed([query])
        except Exception:
            return None
        return vectors[0] if vectors else None

    async def _embed_payload(self, title: str, body: str, tags: list[str]) -> str:
        try:
            vectors = await self._embedding_client.embed([f"{title}\n{body}\n{' '.join(tags)}"])
        except Exception:
            return ""
        if not vectors:
            return ""
        return json.dumps(vectors[0])

    def _score(
        self,
        query: str,
        record: DurableMemoryRecord,
        embedding: list[float] | None,
    ) -> float:
        haystack = " ".join([record.kind, record.title, record.body, " ".join(record.tags)]).lower()
        terms = [item for item in query.lower().split() if item]
        lexical = sum(1.0 for term in terms if term in haystack)
        recency = 0.05 if record.last_used_at else 0.0
        confidence = max(0.0, min(record.confidence, 1.0))
        semantic = 0.0
        if embedding:
            try:
                stored = json.loads(record.embedding_json or "null")
            except Exception:
                stored = None
            if stored is None:
                stored = []
            if isinstance(stored, list) and stored:
                semantic = _cosine_similarity(embedding, [float(item) for item in stored])
        return lexical + semantic + recency + confidence * 0.2


def _row_to_record(row: dict[str, Any]) -> DurableMemoryRecord:
    provenance = _load_json(row.get("provenance_json"), {})
    return DurableMemoryRecord(
        id=str(row.get("id") or ""),
        kind=str(row.get("kind") or "memory"),
        title=str(row.get("title") or ""),
        body=str(row.get("body") or ""),
        tags=_normalize_tags(_load_json(row.get("tags_json"), [])),
        confidence=float(row.get("confidence") or 0.0),
        status=str(row.get("status") or "active"),
        provenance=provenance,
        embedding_json=str(row.get("embedding_json") or "") or None,
        created_at=_parse_datetime(row.get("created_at")),
        updated_at=_parse_datetime(row.get("updated_at")),
        last_used_at=_parse_datetime(row.get("last_used_at")),
    )


def _normalize_tags(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _load_json(value: Any, default: Any) -> Any:
    if not isinstance(value, str) or not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return numerator / (left_norm * right_norm)
