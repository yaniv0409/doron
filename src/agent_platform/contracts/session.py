from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from agent_platform.contracts.api import MissionRunError
from agent_platform.domain.models import CompletionMetadata


class SessionOpenRequest(BaseModel):
    name: str = Field(min_length=1)
    use_dedicated_db: bool = False
    preferred_model: str | None = None
    allowed_models: list[str] | None = None
    output_schema: dict[str, Any] | None = None
    web_enabled: bool = True
    db_mutation_enabled: bool = True
    web_tool_call_limit: int | None = Field(default=None, ge=0)


class SessionUpdateRequest(BaseModel):
    preferred_model: str | None = None
    allowed_models: list[str] | None = None
    output_schema: dict[str, Any] | None = None
    web_enabled: bool | None = None
    db_mutation_enabled: bool | None = None
    web_tool_call_limit: int | None = Field(default=None, ge=0)


class SessionSummaryResponse(BaseModel):
    session_id: str
    name: str
    uses_dedicated_db: bool
    db_path: str
    web_tool_call_limit: int | None = None
    updated_at: str
    created_at: str
    last_trace_id: str | None = None


class SessionTurnResponse(BaseModel):
    message_id: str
    role: str
    content: str
    created_at: str
    trace_id: str | None = None
    status: str
    web_tool_call_limit_used: int | None = None
    completion: CompletionMetadata | None = None


class SessionDetailResponse(SessionSummaryResponse):
    preferred_model: str | None = None
    allowed_models: list[str] | None = None
    output_schema: dict[str, Any] | None = None
    web_enabled: bool
    db_mutation_enabled: bool
    notes: list[str] = Field(default_factory=list)
    recent_tools: list[str] = Field(default_factory=list)
    compression_notice: str | None = None
    turns: list[SessionTurnResponse] = Field(default_factory=list)
    last_error: MissionRunError | None = None


class SessionChatRequest(BaseModel):
    message: str = Field(min_length=1)
    preferred_model: str | None = None
    allowed_models: list[str] | None = None
    output_schema: dict[str, Any] | None = None
    web_enabled: bool | None = None
    db_mutation_enabled: bool | None = None
    web_tool_call_limit: int | None = Field(default=None, ge=0)


class SessionChatResponse(BaseModel):
    session_id: str
    trace_id: str
    status: str
    assistant_message: str
    web_tool_call_limit_used: int | None = None
    completion: CompletionMetadata | None = None
    error: MissionRunError | None = None
    updated_at: str


class GraphNodeResponse(BaseModel):
    id: str
    label: str
    kind: str
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphEdgeResponse(BaseModel):
    id: str
    label: str
    source: str
    target: str
    properties: dict[str, Any] = Field(default_factory=dict)


class SessionGraphResponse(BaseModel):
    session_id: str
    db_path: str
    generated_at: str
    node_count: int
    edge_count: int
    nodes: list[GraphNodeResponse] = Field(default_factory=list)
    edges: list[GraphEdgeResponse] = Field(default_factory=list)


class SessionStreamEvent(BaseModel):
    event: str
    data: dict[str, Any]
