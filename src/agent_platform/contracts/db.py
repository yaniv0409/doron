from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class DbContentsRequest(BaseModel):
    db_path: str = Field(min_length=1)
    sample_limit: int = Field(default=5, ge=1, le=100)
    include_schema: bool = True
    include_counts: bool = True
    include_connections: bool = True


class DbTableContents(BaseModel):
    name: str
    kind: str
    table_schema: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int | None = None
    sample_rows: list[dict[str, Any]] = Field(default_factory=list)
    connection: dict[str, Any] | None = None


class DbContentsResponse(BaseModel):
    db_path: str
    generated_at: str
    sample_limit: int
    include_schema: bool
    include_counts: bool
    include_connections: bool
    table_count: int
    tables: list[DbTableContents] = Field(default_factory=list)
