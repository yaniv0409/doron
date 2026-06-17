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
    ok: bool = True
    error_type: str | None = None
    error_message: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class ToolResult(BaseModel):
    ok: bool
    tool: str
    error_type: str | None = None
    error_message: str | None = None
    retry_hint: str | None = None
    data: Any = None


class DbMutationRecord(BaseModel):
    query: str
    parameters: dict[str, Any]
    summary: str
    created_at: datetime = Field(default_factory=utc_now)


class PageLink(BaseModel):
    text: str
    href: str
    title: str | None = None


class WebArtifact(BaseModel):
    url: str
    title: str | None = None
    summary: str | None = None
    load_state: str | None = None
    browser_stage: str | None = None
    links_count: int = 0
    created_at: datetime = Field(default_factory=utc_now)


class DocumentationLookupRecord(BaseModel):
    query: str
    source_id: str
    excerpt: str
    created_at: datetime = Field(default_factory=utc_now)


class CompressionEvent(BaseModel):
    trigger: str
    summarizer_model: str
    reason: str
    size_before: int
    size_after: int
    preview: str
    original_prompt_preserved: bool = True
    created_at: datetime = Field(default_factory=utc_now)


class RuntimeEvent(BaseModel):
    phase: str
    message: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class ContextTransferPacket(BaseModel):
    mission_prompt: str
    notes: list[str] = Field(default_factory=list)
    db_findings: list[str] = Field(default_factory=list)
    web_findings: list[str] = Field(default_factory=list)
    tool_summaries: list[str] = Field(default_factory=list)
    notice: str | None = None


class CompressedMemory(BaseModel):
    notes: list[str] = Field(default_factory=list)
    db_findings: list[str] = Field(default_factory=list)
    web_findings: list[str] = Field(default_factory=list)
    tool_summaries: list[str] = Field(default_factory=list)
    unresolved_goals: list[str] = Field(default_factory=list)
    notice: str | None = None


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
    compression_events: list[CompressionEvent] = field(default_factory=list)
    runtime_events: list[RuntimeEvent] = field(default_factory=list)
    compressed_memory: CompressedMemory | None = None
    pending_context_refresh_reason: str | None = None
    compression_notice: str | None = None
    compression_in_progress: bool = False
    last_compression_size: int = 0
    pending_model_switch: str | None = None
    browser_session_started: bool = False
    db_checkpoint_path: str | None = None
    progress_hook: Any | None = None

    def build_transfer_packet(self) -> ContextTransferPacket:
        if self.compressed_memory is not None:
            return ContextTransferPacket(
                mission_prompt=self.mission_request.prompt,
                notes=self.compressed_memory.notes,
                db_findings=self.compressed_memory.db_findings,
                web_findings=self.compressed_memory.web_findings,
                tool_summaries=self._build_tool_summary_block(),
                notice=self.compression_notice,
            )
        return ContextTransferPacket(
            mission_prompt=self.mission_request.prompt,
            notes=self.reasoning_notes[-20:],
            db_findings=self.db_findings[-20:],
            web_findings=self.web_findings[-20:],
            tool_summaries=self._build_tool_summary_block(),
            notice=self.compression_notice,
        )

    def estimate_working_memory_size(self) -> int:
        parts = [
            self.mission_request.prompt,
            "\n".join(self.reasoning_notes),
            "\n".join(self.db_findings),
            "\n".join(self.web_findings),
            "\n".join(self.tool_summaries),
        ]
        if self.compressed_memory is not None:
            parts.extend(
                [
                    "\n".join(self.compressed_memory.notes),
                    "\n".join(self.compressed_memory.db_findings),
                    "\n".join(self.compressed_memory.web_findings),
                    "\n".join(self.compressed_memory.tool_summaries),
                    "\n".join(self.compressed_memory.unresolved_goals),
                ]
            )
        return sum(len(part) for part in parts)

    def _build_tool_summary_block(self) -> list[str]:
        summaries = list(self.tool_summaries[-40:])
        if self.compressed_memory is not None:
            summaries.extend(self.compressed_memory.tool_summaries)
            summaries.extend(f"unresolved: {item}" for item in self.compressed_memory.unresolved_goals)
        if self.compression_notice:
            summaries.append(self.compression_notice)
        return summaries


class ExecutionTrace(BaseModel):
    trace_id: str
    request: MissionRequest
    model_sequence: list[str]
    tool_calls: list[ToolCallRecord]
    db_mutations: list[DbMutationRecord]
    docs_lookups: list[DocumentationLookupRecord]
    web_artifacts: list[WebArtifact]
    compression_events: list[CompressionEvent]
    runtime_events: list[RuntimeEvent]
    result: Any = None
    error: MissionError | None = None
    started_at: datetime
    completed_at: datetime
