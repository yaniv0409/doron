from __future__ import annotations

from typing import Any

from agent_platform.config.settings import SessionSettings
from agent_platform.contracts.session import GraphEdgeResponse, GraphNodeResponse, SessionGraphResponse
from agent_platform.domain.models import utc_now
from agent_platform.infrastructure.kuzu_client import KuzuGateway


class GraphSnapshotService:
    def __init__(self, settings: SessionSettings) -> None:
        self._settings = settings

    def build_snapshot(self, session_id: str, db_path: str) -> SessionGraphResponse:
        try:
            gateway = KuzuGateway(db_path, read_only=True)
            tables = gateway.show_tables()
        except Exception:
            return SessionGraphResponse(
                session_id=session_id,
                db_path=db_path,
                generated_at=utc_now().isoformat(),
                node_count=0,
                edge_count=0,
                node_limit=self._settings.graph_node_limit,
                edge_limit=self._settings.graph_edge_limit,
                nodes=[],
                edges=[],
            )
        node_map: dict[str, GraphNodeResponse] = {}
        edge_map: dict[str, GraphEdgeResponse] = {}
        node_limit = max(0, self._settings.graph_node_limit)
        edge_limit = max(0, self._settings.graph_edge_limit)
        is_truncated = False

        for table in tables:
            name = str(table.get("name", ""))
            kind = str(table.get("type", ""))
            if kind == "REL" and len(edge_map) >= edge_limit:
                is_truncated = True
                continue
            if kind != "REL" and len(node_map) >= node_limit:
                is_truncated = True
                continue
            rows = self._load_rows(gateway, name, kind, node_limit=node_limit, edge_limit=edge_limit, node_count=len(node_map), edge_count=len(edge_map))
            if kind == "REL":
                is_truncated = self._collect_relationship_rows(rows, name, node_map, edge_map, node_limit, edge_limit) or is_truncated
            else:
                is_truncated = self._collect_node_rows(rows, name, node_map, node_limit) or is_truncated

        return SessionGraphResponse(
            session_id=session_id,
            db_path=db_path,
            generated_at=utc_now().isoformat(),
            node_count=len(node_map),
            edge_count=len(edge_map),
            node_limit=node_limit,
            edge_limit=edge_limit,
            is_truncated=is_truncated,
            nodes=list(node_map.values()),
            edges=list(edge_map.values()),
        )

    def _load_rows(
        self,
        gateway: KuzuGateway,
        table_name: str,
        kind: str,
        *,
        node_limit: int,
        edge_limit: int,
        node_count: int,
        edge_count: int,
    ) -> list[dict[str, Any]]:
        if not table_name:
            return []
        remaining = edge_limit - edge_count if kind == "REL" else node_limit - node_count
        if remaining <= 0:
            return []
        if kind == "REL":
            return gateway.sample_rows(table_name, kind=kind, limit=remaining)
        return gateway.sample_rows(table_name, kind=kind, limit=remaining)

    def _collect_node_rows(
        self,
        rows: list[dict[str, Any]],
        table_name: str,
        node_map: dict[str, GraphNodeResponse],
        node_limit: int,
    ) -> bool:
        truncated = False
        for row in rows:
            if len(node_map) >= node_limit:
                truncated = True
                break
            node = row.get("n")
            response = self._node_from_value(node, table_name)
            node_map[response.id] = response
        if len(rows) >= max(0, node_limit):
            truncated = truncated or len(node_map) >= node_limit
        return truncated

    def _collect_relationship_rows(
        self,
        rows: list[dict[str, Any]],
        table_name: str,
        node_map: dict[str, GraphNodeResponse],
        edge_map: dict[str, GraphEdgeResponse],
        node_limit: int,
        edge_limit: int,
    ) -> bool:
        truncated = False
        for row in rows:
            if len(edge_map) >= edge_limit:
                truncated = True
                break
            source = self._node_from_value(row.get("a"), "source")
            target = self._node_from_value(row.get("b"), "target")
            required_nodes = int(source.id not in node_map) + int(target.id not in node_map)
            if len(node_map) + required_nodes > node_limit:
                truncated = True
                continue
            node_map[source.id] = source
            node_map[target.id] = target
            edge = self._edge_from_value(row.get("r"), table_name, source.id, target.id)
            edge_map[edge.id] = edge
        if len(rows) >= max(0, edge_limit):
            truncated = truncated or len(edge_map) >= edge_limit
        return truncated

    def _node_from_value(self, value: Any, fallback_label: str) -> GraphNodeResponse:
        attrs = _object_attrs(value)
        node_id = str(
            attrs.get("_id")
            or attrs.get("id")
            or attrs.get("element_id")
            or attrs.get("offset")
            or f"{fallback_label}:{repr(value)}"
        )
        label = _first_text(
            attrs.get("label"),
            attrs.get("_label"),
            attrs.get("name"),
            attrs.get("title"),
            fallback_label,
        )
        kind = _first_text(attrs.get("_label"), attrs.get("label"), fallback_label)
        properties = _jsonable(attrs.get("_properties") or attrs.get("properties") or value)
        if not isinstance(properties, dict):
            properties = {"value": properties}
        return GraphNodeResponse(id=node_id, label=label, kind=kind, properties=properties)

    def _edge_from_value(self, value: Any, fallback_label: str, source: str, target: str) -> GraphEdgeResponse:
        attrs = _object_attrs(value)
        edge_id = str(
            attrs.get("_id")
            or attrs.get("id")
            or attrs.get("element_id")
            or f"{fallback_label}:{source}:{target}"
        )
        label = _first_text(attrs.get("_label"), attrs.get("label"), fallback_label)
        properties = _jsonable(attrs.get("_properties") or attrs.get("properties") or value)
        if not isinstance(properties, dict):
            properties = {"value": properties}
        return GraphEdgeResponse(
            id=edge_id,
            label=label,
            source=source,
            target=target,
            properties=properties,
        )


def _quote_identifier(name: str) -> str:
    return f"`{name.replace('`', '``')}`"


def _object_attrs(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if hasattr(value, "__dict__"):
        return dict(vars(value))
    result: dict[str, Any] = {}
    for name in ("_id", "_label", "_properties", "id", "label", "properties", "name", "title"):
        if hasattr(value, name):
            result[name] = getattr(value, name)
    return result


def _first_text(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value:
            return value
    return "item"


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    attrs = _object_attrs(value)
    if attrs:
        return {str(key): _jsonable(item) for key, item in attrs.items()}
    return repr(value)
