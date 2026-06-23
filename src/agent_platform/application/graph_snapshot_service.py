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
                nodes=[],
                edges=[],
            )
        node_map: dict[str, GraphNodeResponse] = {}
        edge_map: dict[str, GraphEdgeResponse] = {}

        for table in tables:
            name = str(table.get("name", ""))
            kind = str(table.get("type", ""))
            rows = self._load_rows(gateway, name, kind)
            if kind == "REL":
                self._collect_relationship_rows(rows, name, node_map, edge_map)
            else:
                self._collect_node_rows(rows, name, node_map)

        return SessionGraphResponse(
            session_id=session_id,
            db_path=db_path,
            generated_at=utc_now().isoformat(),
            node_count=len(node_map),
            edge_count=len(edge_map),
            nodes=list(node_map.values()),
            edges=list(edge_map.values()),
        )

    def _load_rows(self, gateway: KuzuGateway, table_name: str, kind: str) -> list[dict[str, Any]]:
        if not table_name:
            return []
        if kind == "REL":
            return gateway.execute(f"MATCH (a)-[r:{_quote_identifier(table_name)}]->(b) RETURN a, r, b;")
        return gateway.execute(f"MATCH (n:{_quote_identifier(table_name)}) RETURN n;")

    def _collect_node_rows(
        self,
        rows: list[dict[str, Any]],
        table_name: str,
        node_map: dict[str, GraphNodeResponse],
    ) -> None:
        for row in rows:
            node = row.get("n")
            response = self._node_from_value(node, table_name)
            node_map[response.id] = response

    def _collect_relationship_rows(
        self,
        rows: list[dict[str, Any]],
        table_name: str,
        node_map: dict[str, GraphNodeResponse],
        edge_map: dict[str, GraphEdgeResponse],
    ) -> None:
        for row in rows:
            source = self._node_from_value(row.get("a"), "source")
            target = self._node_from_value(row.get("b"), "target")
            node_map[source.id] = source
            node_map[target.id] = target
            edge = self._edge_from_value(row.get("r"), table_name, source.id, target.id)
            edge_map[edge.id] = edge

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
