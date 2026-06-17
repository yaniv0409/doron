from __future__ import annotations

from fastapi import APIRouter, Request

from agent_platform.application.db_snapshot_service import DbSnapshotService
from agent_platform.contracts.db import DbContentsRequest, DbContentsResponse

router = APIRouter(tags=["db"])


@router.post("/db/contents", response_model=DbContentsResponse)
async def read_db_contents(request: DbContentsRequest, http_request: Request) -> DbContentsResponse:
    logger = http_request.app.state.logger
    service: DbSnapshotService = http_request.app.state.db_contents_service
    logger.info(
        "db contents requested",
        extra={
            "trace_id": "-",
            "db_path": request.db_path,
            "sample_limit": request.sample_limit,
            "include_schema": request.include_schema,
            "include_counts": request.include_counts,
            "include_connections": request.include_connections,
        },
    )
    snapshot = service.build_snapshot(request)
    logger.info(
        "db contents returned",
        extra={
            "trace_id": "-",
            "db_path": request.db_path,
            "table_count": snapshot.table_count,
        },
    )
    return snapshot
