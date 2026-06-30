from __future__ import annotations

import math
import re
import uuid
from collections import Counter, defaultdict, deque
from datetime import datetime
from typing import Any

from agent_platform.domain.exceptions import DatabaseError, RequestValidationError
from agent_platform.domain.models import ResearchEdgeRecord, ResearchNodeRecord, utc_now
from agent_platform.infrastructure.kuzu_client import KuzuGateway


class ResearchGraphManager:
    def ensure_schema(self, db: KuzuGateway) -> None:
        try:
            table_names = set(db.table_names())
        except DatabaseError:
            table_names = set()
        if "ResearchNode" not in table_names:
            db.execute(
                """
                CREATE NODE TABLE ResearchNode(
                    id STRING,
                    kind STRING,
                    title STRING,
                    body STRING,
                    status STRING,
                    priority INT64,
                    confidence DOUBLE,
                    failure_kind STRING,
                    is_root BOOL,
                    created_at STRING,
                    updated_at STRING,
                    closed_at STRING,
                    source_trace_id STRING,
                    PRIMARY KEY(id)
                );
                """
            )
        if "ResearchEdge" not in table_names:
            db.execute(
                """
                CREATE REL TABLE ResearchEdge(
                    FROM ResearchNode TO ResearchNode,
                    id STRING,
                    kind STRING,
                    content STRING,
                    transition_kind STRING,
                    confidence DOUBLE,
                    evidence_kind STRING,
                    created_at STRING,
                    source_trace_id STRING
                );
                """
            )

    def ensure_ready(self, db: KuzuGateway, prompt: str, *, source_trace_id: str | None = None) -> ResearchNodeRecord | None:
        self.ensure_schema(db)
        existing = self.root(db)
        if existing is not None:
            return existing
        prompt = prompt.strip()
        if not prompt:
            return None
        return self._create_node(
            db,
            {
                "kind": "task",
                "title": _summarize_title(prompt),
                "body": prompt.strip(),
                "status": "open",
                "is_root": True,
                "source_trace_id": source_trace_id,
            },
        )

    def ensure_root(self, db: KuzuGateway, prompt: str, *, source_trace_id: str | None = None) -> ResearchNodeRecord:
        root = self.ensure_ready(db, prompt, source_trace_id=source_trace_id)
        if root is None:
            raise RequestValidationError("research graph root prompt is required")
        return root

    def root(self, db: KuzuGateway) -> ResearchNodeRecord | None:
        self.ensure_schema(db)
        rows = db.execute(
            """
            MATCH (node:ResearchNode)
            WHERE node.is_root = true
            RETURN
                node.id AS id,
                node.kind AS kind,
                node.title AS title,
                node.body AS body,
                node.status AS status,
                node.priority AS priority,
                node.confidence AS confidence,
                node.failure_kind AS failure_kind,
                node.is_root AS is_root,
                node.created_at AS created_at,
                node.updated_at AS updated_at,
                node.closed_at AS closed_at,
                node.source_trace_id AS source_trace_id
            LIMIT 1;
            """
        )
        if not rows:
            return None
        return _row_to_node(rows[0])

    def advance_new(
        self,
        db: KuzuGateway,
        from_node_id: str,
        edge: dict[str, Any],
        new_node: dict[str, Any],
        *,
        prompt: str,
        source_trace_id: str | None = None,
    ) -> dict[str, Any]:
        self.ensure_ready(db, prompt, source_trace_id=source_trace_id)
        source = self._require_node(db, from_node_id)
        edge_kind = _normalize_edge_kind(edge.get("kind"))
        node_kind = _normalize_node_kind(new_node.get("kind"))
        if edge_kind == "dead_end" and node_kind != "failure":
            raise RequestValidationError("dead_end edges must point to failure nodes")
        if edge_kind == "progress" and node_kind == "failure":
            raise RequestValidationError("progress edges cannot point to failure nodes")
        created_node = self._create_node(
            db,
            {
                **new_node,
                "kind": node_kind,
                "status": _normalize_node_status(new_node.get("status"), node_kind),
                "source_trace_id": source_trace_id or new_node.get("source_trace_id"),
            },
        )
        created_edge = self._create_edge(
            db,
            from_node_id=source.id,
            to_node_id=created_node.id,
            payload={
                **edge,
                "kind": edge_kind,
                "transition_kind": _normalize_transition_kind(edge.get("transition_kind"), edge_kind),
                "source_trace_id": source_trace_id or edge.get("source_trace_id"),
            },
        )
        return {
            "source_node": source.model_dump(mode="json"),
            "edge": created_edge.model_dump(mode="json"),
            "target_node": created_node.model_dump(mode="json"),
        }

    def advance_existing(
        self,
        db: KuzuGateway,
        from_node_id: str,
        to_node_id: str,
        edge: dict[str, Any],
        *,
        prompt: str,
        source_trace_id: str | None = None,
    ) -> dict[str, Any]:
        self.ensure_ready(db, prompt, source_trace_id=source_trace_id)
        source = self._require_node(db, from_node_id)
        target = self._require_node(db, to_node_id)
        edge_kind = _normalize_edge_kind(edge.get("kind"))
        if edge_kind == "dead_end" and target.kind != "failure":
            raise RequestValidationError("dead_end edges must point to failure nodes")
        if edge_kind == "progress" and target.kind == "failure":
            raise RequestValidationError("progress edges cannot point to failure nodes")
        created_edge = self._create_edge(
            db,
            from_node_id=source.id,
            to_node_id=target.id,
            payload={
                **edge,
                "kind": edge_kind,
                "transition_kind": _normalize_transition_kind(edge.get("transition_kind"), edge_kind),
                "source_trace_id": source_trace_id or edge.get("source_trace_id"),
            },
        )
        return {
            "source_node": source.model_dump(mode="json"),
            "edge": created_edge.model_dump(mode="json"),
            "target_node": target.model_dump(mode="json"),
        }

    def get_frontier(self, db: KuzuGateway, *, prompt: str, source_trace_id: str | None = None) -> list[ResearchNodeRecord]:
        self.ensure_ready(db, prompt, source_trace_id=source_trace_id)
        nodes = {node.id: node for node in self._list_nodes(db)}
        outgoing = {edge.from_node_id for edge in self._list_edges(db)}
        frontier: list[ResearchNodeRecord] = []
        for node in nodes.values():
            if node.kind == "failure":
                continue
            if node.status in {"resolved", "terminal", "abandoned"}:
                continue
            if node.id in outgoing:
                continue
            frontier.append(node)
        frontier.sort(key=lambda item: item.created_at or datetime.min, reverse=True)
        return frontier

    def get_ancestry(
        self,
        db: KuzuGateway,
        node_id: str,
        depth: int,
        *,
        prompt: str,
        source_trace_id: str | None = None,
    ) -> list[dict[str, Any]]:
        self.ensure_ready(db, prompt, source_trace_id=source_trace_id)
        nodes = {node.id: node for node in self._list_nodes(db)}
        edges = self._list_edges(db)
        incoming: dict[str, list[ResearchEdgeRecord]] = defaultdict(list)
        for edge in edges:
            incoming[edge.to_node_id].append(edge)
        transitions: list[dict[str, Any]] = []
        queue: deque[tuple[str, int]] = deque([(node_id, 0)])
        seen_edges: set[str] = set()
        while queue:
            current_id, current_depth = queue.popleft()
            if current_depth >= depth:
                continue
            for edge in incoming.get(current_id, []):
                if edge.id in seen_edges:
                    continue
                seen_edges.add(edge.id)
                parent = nodes.get(edge.from_node_id)
                child = nodes.get(edge.to_node_id)
                if parent is None or child is None:
                    continue
                transitions.append(_transition_payload(parent, edge, child, current_depth + 1))
                queue.append((parent.id, current_depth + 1))
        return transitions

    def get_descendants(
        self,
        db: KuzuGateway,
        node_id: str,
        depth: int,
        mode: str = "all",
        *,
        prompt: str,
        source_trace_id: str | None = None,
    ) -> list[dict[str, Any]]:
        self.ensure_ready(db, prompt, source_trace_id=source_trace_id)
        nodes = {node.id: node for node in self._list_nodes(db)}
        edges = self._list_edges(db)
        outgoing: dict[str, list[ResearchEdgeRecord]] = defaultdict(list)
        for edge in edges:
            outgoing[edge.from_node_id].append(edge)
        frontier_ids = {
            item.id
            for item in self.get_frontier(db, prompt=prompt, source_trace_id=source_trace_id)
        }
        transitions: list[dict[str, Any]] = []
        queue: deque[tuple[str, int]] = deque([(node_id, 0)])
        seen_edges: set[str] = set()
        while queue:
            current_id, current_depth = queue.popleft()
            if current_depth >= depth:
                continue
            for edge in outgoing.get(current_id, []):
                if edge.id in seen_edges:
                    continue
                seen_edges.add(edge.id)
                parent = nodes.get(edge.from_node_id)
                child = nodes.get(edge.to_node_id)
                if parent is None or child is None:
                    continue
                if _descendant_mode_matches(mode, child, frontier_ids):
                    transitions.append(_transition_payload(parent, edge, child, current_depth + 1))
                queue.append((child.id, current_depth + 1))
        return transitions

    def search_nodes(
        self,
        db: KuzuGateway,
        query: str,
        *,
        limit: int,
        include_failures: bool = False,
        prompt: str,
        source_trace_id: str | None = None,
    ) -> list[ResearchNodeRecord]:
        self.ensure_ready(db, prompt, source_trace_id=source_trace_id)
        nodes = self._list_nodes(db)
        if not include_failures:
            nodes = [node for node in nodes if node.kind != "failure"]
        if not query.strip():
            return nodes[:limit]
        tokens = _tokenize(query)
        if not tokens:
            return nodes[:limit]
        incoming_text = self._incoming_edge_text(db)
        corpus = {
            node.id: _tokenize(" ".join([node.kind, node.title, node.body, incoming_text.get(node.id, "")]))
            for node in nodes
        }
        doc_count = max(1, len(corpus))
        doc_freq: Counter[str] = Counter()
        for terms in corpus.values():
            doc_freq.update(set(terms))
        scores: list[ResearchNodeRecord] = []
        for node in nodes:
            terms = corpus.get(node.id, [])
            if not terms:
                continue
            term_counts = Counter(terms)
            score = 0.0
            for token in tokens:
                frequency = term_counts.get(token, 0)
                if frequency <= 0:
                    continue
                idf = math.log(1 + (doc_count - doc_freq[token] + 0.5) / (doc_freq[token] + 0.5))
                score += idf * (frequency / (frequency + 0.75 + 1.5 * (len(terms) / max(1.0, _avg_doc_length(corpus)))))
            if query.lower() in node.title.lower():
                score += 0.5
            if score <= 0:
                continue
            scores.append(node.model_copy(update={"score": round(score, 6)}))
        scores.sort(key=lambda item: (item.score or 0.0, item.created_at or datetime.min), reverse=True)
        return scores[:limit]

    def create_root_work_item(self, db: KuzuGateway, prompt: str, *, source_trace_id: str | None = None) -> dict[str, Any]:
        root = self.ensure_root(db, prompt, source_trace_id=source_trace_id)
        return root.model_dump(mode="json")

    def _list_nodes(self, db: KuzuGateway) -> list[ResearchNodeRecord]:
        rows = db.execute(
            """
            MATCH (node:ResearchNode)
            RETURN
                node.id AS id,
                node.kind AS kind,
                node.title AS title,
                node.body AS body,
                node.status AS status,
                node.priority AS priority,
                node.confidence AS confidence,
                node.failure_kind AS failure_kind,
                node.is_root AS is_root,
                node.created_at AS created_at,
                node.updated_at AS updated_at,
                node.closed_at AS closed_at,
                node.source_trace_id AS source_trace_id
            LIMIT 5000;
            """
        )
        return [_row_to_node(row) for row in rows]

    def _list_edges(self, db: KuzuGateway) -> list[ResearchEdgeRecord]:
        rows = db.execute(
            """
            MATCH (source:ResearchNode)-[edge:ResearchEdge]->(target:ResearchNode)
            RETURN
                edge.id AS id,
                source.id AS from_node_id,
                target.id AS to_node_id,
                edge.kind AS kind,
                edge.content AS content,
                edge.transition_kind AS transition_kind,
                edge.confidence AS confidence,
                edge.evidence_kind AS evidence_kind,
                edge.created_at AS created_at,
                edge.source_trace_id AS source_trace_id
            LIMIT 10000;
            """
        )
        return [_row_to_edge(row) for row in rows]

    def _incoming_edge_text(self, db: KuzuGateway) -> dict[str, str]:
        incoming: dict[str, list[str]] = defaultdict(list)
        for edge in self._list_edges(db):
            incoming[edge.to_node_id].append(edge.content)
        return {node_id: " ".join(items[-4:]) for node_id, items in incoming.items()}

    def _create_node(self, db: KuzuGateway, payload: dict[str, Any]) -> ResearchNodeRecord:
        now = utc_now().isoformat()
        node_id = str(payload.get("id") or uuid.uuid4())
        kind = _normalize_node_kind(payload.get("kind"))
        body = str(payload.get("body") or "").strip()
        title = str(payload.get("title") or _summarize_title(body)).strip()
        if not title or not body:
            raise RequestValidationError("research nodes require non-empty title and body")
        status = _normalize_node_status(payload.get("status"), kind)
        closed_at = payload.get("closed_at")
        if kind == "failure" and not closed_at:
            closed_at = now
        db.execute(
            """
            CREATE (node:ResearchNode {
                id: $id,
                kind: $kind,
                title: $title,
                body: $body,
                status: $status,
                priority: $priority,
                confidence: $confidence,
                failure_kind: $failure_kind,
                is_root: $is_root,
                created_at: $created_at,
                updated_at: $updated_at,
                closed_at: $closed_at,
                source_trace_id: $source_trace_id
            });
            """,
            {
                "id": node_id,
                "kind": kind,
                "title": title,
                "body": body,
                "status": status,
                "priority": payload.get("priority"),
                "confidence": payload.get("confidence"),
                "failure_kind": payload.get("failure_kind"),
                "is_root": bool(payload.get("is_root")),
                "created_at": now,
                "updated_at": now,
                "closed_at": closed_at,
                "source_trace_id": payload.get("source_trace_id"),
            },
        )
        return ResearchNodeRecord(
            id=node_id,
            kind=kind,
            title=title,
            body=body,
            status=status,
            priority=_optional_int(payload.get("priority")),
            confidence=_optional_float(payload.get("confidence")),
            failure_kind=_optional_str(payload.get("failure_kind")),
            is_root=bool(payload.get("is_root")),
            created_at=_parse_datetime(now),
            updated_at=_parse_datetime(now),
            closed_at=_parse_datetime(closed_at),
            source_trace_id=_optional_str(payload.get("source_trace_id")),
        )

    def _create_edge(
        self,
        db: KuzuGateway,
        *,
        from_node_id: str,
        to_node_id: str,
        payload: dict[str, Any],
    ) -> ResearchEdgeRecord:
        now = utc_now().isoformat()
        edge_id = str(payload.get("id") or uuid.uuid4())
        content = str(payload.get("content") or "").strip()
        if not content:
            raise RequestValidationError("research edges require non-empty content")
        edge_kind = _normalize_edge_kind(payload.get("kind"))
        transition_kind = _normalize_transition_kind(payload.get("transition_kind"), edge_kind)
        db.execute(
            """
            MATCH (source:ResearchNode), (target:ResearchNode)
            WHERE source.id = $from_node_id AND target.id = $to_node_id
            CREATE (source)-[:ResearchEdge {
                id: $id,
                kind: $kind,
                content: $content,
                transition_kind: $transition_kind,
                confidence: $confidence,
                evidence_kind: $evidence_kind,
                created_at: $created_at,
                source_trace_id: $source_trace_id
            }]->(target);
            """,
            {
                "from_node_id": from_node_id,
                "to_node_id": to_node_id,
                "id": edge_id,
                "kind": edge_kind,
                "content": content,
                "transition_kind": transition_kind,
                "confidence": payload.get("confidence"),
                "evidence_kind": payload.get("evidence_kind"),
                "created_at": now,
                "source_trace_id": payload.get("source_trace_id"),
            },
        )
        return ResearchEdgeRecord(
            id=edge_id,
            from_node_id=from_node_id,
            to_node_id=to_node_id,
            kind=edge_kind,
            content=content,
            transition_kind=transition_kind,
            confidence=_optional_float(payload.get("confidence")),
            evidence_kind=_optional_str(payload.get("evidence_kind")),
            created_at=_parse_datetime(now),
            source_trace_id=_optional_str(payload.get("source_trace_id")),
        )

    def _require_node(self, db: KuzuGateway, node_id: str) -> ResearchNodeRecord:
        for node in self._list_nodes(db):
            if node.id == node_id:
                return node
        raise RequestValidationError(f"unknown research node: {node_id}")


def _row_to_node(row: dict[str, Any]) -> ResearchNodeRecord:
    return ResearchNodeRecord(
        id=str(row.get("id") or ""),
        kind=str(row.get("kind") or "task"),
        title=str(row.get("title") or ""),
        body=str(row.get("body") or ""),
        status=str(row.get("status") or "open"),
        priority=_optional_int(row.get("priority")),
        confidence=_optional_float(row.get("confidence")),
        failure_kind=_optional_str(row.get("failure_kind")),
        is_root=bool(row.get("is_root")),
        created_at=_parse_datetime(row.get("created_at")),
        updated_at=_parse_datetime(row.get("updated_at")),
        closed_at=_parse_datetime(row.get("closed_at")),
        source_trace_id=_optional_str(row.get("source_trace_id")),
    )


def _row_to_edge(row: dict[str, Any]) -> ResearchEdgeRecord:
    return ResearchEdgeRecord(
        id=str(row.get("id") or ""),
        from_node_id=str(row.get("from_node_id") or ""),
        to_node_id=str(row.get("to_node_id") or ""),
        kind=str(row.get("kind") or "progress"),
        content=str(row.get("content") or ""),
        transition_kind=str(row.get("transition_kind") or "finding"),
        confidence=_optional_float(row.get("confidence")),
        evidence_kind=_optional_str(row.get("evidence_kind")),
        created_at=_parse_datetime(row.get("created_at")),
        source_trace_id=_optional_str(row.get("source_trace_id")),
    )


def _normalize_node_kind(value: Any) -> str:
    kind = str(value or "task").strip().lower()
    if kind not in {"task", "question", "failure"}:
        raise RequestValidationError(f"invalid research node kind: {kind}")
    return kind


def _normalize_edge_kind(value: Any) -> str:
    kind = str(value or "progress").strip().lower()
    if kind not in {"progress", "dead_end"}:
        raise RequestValidationError(f"invalid research edge kind: {kind}")
    return kind


def _normalize_transition_kind(value: Any, edge_kind: str) -> str:
    transition_kind = str(value or ("failure" if edge_kind == "dead_end" else "finding")).strip().lower()
    allowed = {"finding", "answer", "idea", "decision", "failure"}
    if transition_kind not in allowed:
        raise RequestValidationError(f"invalid transition kind: {transition_kind}")
    return transition_kind


def _normalize_node_status(value: Any, node_kind: str) -> str:
    if node_kind == "failure":
        return "terminal"
    status = str(value or "open").strip().lower()
    if status not in {"open", "resolved", "terminal", "abandoned"}:
        raise RequestValidationError(f"invalid research node status: {status}")
    return status


def _descendant_mode_matches(mode: str, child: ResearchNodeRecord, frontier_ids: set[str]) -> bool:
    normalized = str(mode or "all").strip().lower()
    if normalized == "all":
        return True
    if normalized == "frontier":
        return child.id in frontier_ids
    if normalized == "terminal":
        return child.kind == "failure" or child.status in {"terminal", "abandoned"}
    raise RequestValidationError(f"invalid descendant mode: {mode}")


def _transition_payload(
    parent: ResearchNodeRecord,
    edge: ResearchEdgeRecord,
    child: ResearchNodeRecord,
    depth: int,
) -> dict[str, Any]:
    return {
        "depth": depth,
        "from_node": parent.model_dump(mode="json"),
        "edge": edge.model_dump(mode="json"),
        "to_node": child.model_dump(mode="json"),
    }


def _tokenize(text: str) -> list[str]:
    return [item for item in re.findall(r"[a-z0-9]+", text.lower()) if item]


def _avg_doc_length(corpus: dict[str, list[str]]) -> float:
    if not corpus:
        return 1.0
    return max(1.0, sum(len(tokens) for tokens in corpus.values()) / len(corpus))


def _summarize_title(text: str) -> str:
    compact = " ".join(text.strip().split())
    if len(compact) <= 120:
        return compact
    return compact[:117].rstrip() + "..."


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _optional_str(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)
