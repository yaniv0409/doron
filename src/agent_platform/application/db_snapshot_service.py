from __future__ import annotations

from agent_platform.contracts.db import DbContentsRequest, DbContentsResponse, DbTableContents
from agent_platform.domain.models import utc_now
from agent_platform.infrastructure.kuzu_client import KuzuGateway


class DbSnapshotService:
    def build_snapshot(self, request: DbContentsRequest) -> DbContentsResponse:
        gateway = KuzuGateway(request.db_path, read_only=True)
        tables = gateway.show_tables()
        discovered_tables = self._build_tables(gateway, tables, request)
        return DbContentsResponse(
            db_path=request.db_path,
            generated_at=utc_now().isoformat(),
            sample_limit=request.sample_limit,
            include_schema=request.include_schema,
            include_counts=request.include_counts,
            include_connections=request.include_connections,
            table_count=len(discovered_tables),
            tables=discovered_tables,
        )

    def _build_tables(
        self,
        gateway: KuzuGateway,
        tables: list[dict[str, object]],
        request: DbContentsRequest,
    ) -> list[DbTableContents]:
        result: list[DbTableContents] = []
        for table in tables:
            result.append(self._build_table(gateway, table, request))
        return result

    def _build_table(
        self,
        gateway: KuzuGateway,
        table: dict[str, object],
        request: DbContentsRequest,
    ) -> DbTableContents:
        name = str(table.get("name", ""))
        kind = str(table.get("type", ""))
        schema = gateway.table_info(name) if request.include_schema else []
        connection = gateway.show_connection(name)[0] if kind == "REL" and request.include_connections else None
        row_count = gateway.count_rows(name, kind=kind) if request.include_counts else None
        sample_rows = gateway.sample_rows(name, kind=kind, limit=request.sample_limit)
        return DbTableContents(
            name=name,
            kind=kind,
            table_schema=schema,
            row_count=row_count,
            sample_rows=sample_rows,
            connection=connection,
        )
