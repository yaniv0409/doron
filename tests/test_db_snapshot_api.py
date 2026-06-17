from __future__ import annotations

import asyncio

from httpx import ASGITransport, AsyncClient

from agent_platform.api.app import create_app
from agent_platform.contracts.db import DbContentsResponse, DbTableContents


def test_db_contents_endpoint_returns_snapshot() -> None:
    async def scenario() -> dict[str, object]:
        app = create_app()

        class FakeService:
            def build_snapshot(self, request):
                return DbContentsResponse(
                    db_path=request.db_path,
                    generated_at="2026-06-17T00:00:00Z",
                    sample_limit=request.sample_limit,
                    include_schema=request.include_schema,
                    include_counts=request.include_counts,
                    include_connections=request.include_connections,
                    table_count=1,
                    tables=[
                        DbTableContents(
                            name="Company",
                            kind="NODE",
                            table_schema=[{"name": "name", "type": "STRING"}],
                            row_count=1,
                            sample_rows=[{"name": "A"}],
                        )
                    ],
                )

        app.state.db_contents_service = FakeService()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/db/contents",
                json={"db_path": "/tmp/db.kuzu", "sample_limit": 2},
            )

        assert response.status_code == 200
        return response.json()

    payload = asyncio.run(scenario())
    assert payload["db_path"] == "/tmp/db.kuzu"
    assert payload["table_count"] == 1
    assert payload["tables"][0]["name"] == "Company"


def test_db_contents_endpoint_rejects_bad_paths() -> None:
    async def scenario() -> dict[str, object]:
        app = create_app()

        class FailingService:
            def build_snapshot(self, request):
                from agent_platform.domain.exceptions import DatabaseError

                raise DatabaseError("missing db")

        app.state.db_contents_service = FailingService()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/db/contents",
                json={"db_path": "/tmp/missing.kuzu", "sample_limit": 2},
            )

        assert response.status_code == 400
        return response.json()

    payload = asyncio.run(scenario())
    assert payload["error"]["code"] == "DatabaseError"
