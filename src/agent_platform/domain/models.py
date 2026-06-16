from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from agent_platform.domain.enums import MissionStatus, ResultFormat


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class MissionRequest(BaseModel):
    prompt: str = Field(min_length=1)
    db_path: str = Field(min_length=1)
    output_schema: dict[str, Any] | None = None
    preferred_model: str | None = None
    allowed_models: list[str] | None = None
    mission_metadata: dict[str, Any] | None = None
    web_enabled: bool = True
    db_mutation_enabled: bool = True


class MissionError(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class MissionResult(BaseModel):
    status: MissionStatus
    result: Any = None
    result_format: ResultFormat
    final_model: str
    trace_id: str
    started_at: datetime
    completed_at: datetime
    error: MissionError | None = None


class ModelDescriptor(BaseModel):
    name: str
    rank: int
    context_window: int | None = None
    cost_class: str = "standard"
    supports_tools: bool = True
    supports_structured_output: bool = True
    is_default: bool = False


class ToolCallRecord(BaseModel):
    name: str
    arguments: dict[str, Any]
    result_summary: str
    created_at: datetime = Field(default_factory=utc_now)


class DbMutationRecord(BaseModel):
    query: str
    parameters: dict[str, Any]
    summary: str
    created_at: datetime = Field(default_factory=utc_now)


class WebArtifact(BaseModel):
    url: str
    title: str | None = None
    summary: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class DocumentationLookupRecord(BaseModel):
    query: str
    source_id: str
    excerpt: str
    created_at: datetime = Field(default_factory=utc_now)


class ContextTransferPacket(BaseModel):
    mission_prompt: str
    notes: list[str] = Field(default_factory=list)
    db_findings: list[str] = Field(default_factory=list)
    web_findings: list[str] = Field(default_factory=list)
    tool_summaries: list[str] = Field(default_factory=list)


@dataclass(slots=True)
class RuntimeContext:
    trace_id: str
    mission_request: MissionRequest
    started_at: datetime
    current_model: ModelDescriptor
    allowed_models: list[ModelDescriptor]
    reasoning_notes: list[str] = field(default_factory=list)
    db_findings: list[str] = field(default_factory=list)
    web_findings: list[str] = field(default_factory=list)
    web_artifacts: list[WebArtifact] = field(default_factory=list)
    tool_summaries: list[str] = field(default_factory=list)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    db_mutations: list[DbMutationRecord] = field(default_factory=list)
    docs_lookups: list[DocumentationLookupRecord] = field(default_factory=list)
    pending_model_switch: str | None = None
    browser_session_started: bool = False
    db_checkpoint_path: str | None = None

    def build_transfer_packet(self) -> ContextTransferPacket:
        return ContextTransferPacket(
            mission_prompt=self.mission_request.prompt,
            notes=self.reasoning_notes[-20:],
            db_findings=self.db_findings[-20:],
            web_findings=self.web_findings[-20:],
            tool_summaries=self.tool_summaries[-40:],
        )


class ExecutionTrace(BaseModel):
    trace_id: str
    request: MissionRequest
    model_sequence: list[str]
    tool_calls: list[ToolCallRecord]
    db_mutations: list[DbMutationRecord]
    docs_lookups: list[DocumentationLookupRecord]
    web_artifacts: list[WebArtifact]
    result: Any = None
    error: MissionError | None = None
    started_at: datetime
    completed_at: datetime
