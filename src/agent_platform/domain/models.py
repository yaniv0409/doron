from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from agent_platform.domain.enums import MaintenanceJobStatus, MissionStatus, ResultFormat


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
    web_tool_call_limit: int | None = Field(default=None, ge=0)


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


class MaintenanceJobRecord(BaseModel):
    trace_id: str
    parent_trace_id: str
    request: MissionRequest
    status: MaintenanceJobStatus = MaintenanceJobStatus.PENDING
    attempt_count: int = 0
    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    last_error: MissionError | None = None


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
    reason: str | None = None
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


class WebSearchHit(BaseModel):
    title: str
    url: str
    snippet: str


class WebSearchResponse(BaseModel):
    query: str
    reason: str
    source: str = "duckduckgo"
    hits: list[WebSearchHit] = Field(default_factory=list)


class WebArtifact(BaseModel):
    url: str
    title: str | None = None
    summary: str | None = None
    load_state: str | None = None
    browser_stage: str | None = None
    links_count: int = 0
    created_at: datetime = Field(default_factory=utc_now)


class WebFetchResult(BaseModel):
    requested_url: str
    url: str | None = None
    ok: bool = True
    title: str | None = None
    text: str | None = None
    links: list[PageLink] = Field(default_factory=list)
    load_state: str | None = None
    browser_stage: str | None = None
    error_type: str | None = None
    error_message: str | None = None
    retry_hint: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class WebFetchBatchResult(BaseModel):
    reason: str
    requested_urls: list[str]
    results: list[WebFetchResult]
    successful_count: int
    failed_count: int
    max_workers: int
    web_tool_calls_used: int
    web_tool_calls_remaining: int


class DocumentationLookupRecord(BaseModel):
    query: str
    source_id: str
    excerpt: str
    created_at: datetime = Field(default_factory=utc_now)


class DurableMemoryRecord(BaseModel):
    id: str
    kind: str
    title: str
    body: str
    tags: list[str] = Field(default_factory=list)
    confidence: float = 0.5
    status: str = "active"
    provenance: dict[str, Any] = Field(default_factory=dict)
    embedding_json: str | None = None
    score: float | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    last_used_at: datetime | None = None


class MemoryRetrievalRecord(BaseModel):
    query: str
    kinds: list[str] = Field(default_factory=list)
    results: list[DurableMemoryRecord] = Field(default_factory=list)
    source: str = "runtime"
    created_at: datetime = Field(default_factory=utc_now)


class MemoryMutationRecord(BaseModel):
    action: str
    memory_id: str
    kind: str
    title: str
    reason: str
    status: str = "active"
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
    worked_tool_patterns: list[str] = Field(default_factory=list)
    failed_tool_patterns: list[str] = Field(default_factory=list)
    notice: str | None = None


class CompressedToolOutcome(BaseModel):
    tool: str
    status: str
    reason: str | None = None
    arguments_summary: str | None = None
    result_summary: str | None = None
    error_type: str | None = None
    error_message: str | None = None
    retry_guidance: str | None = None
    repeat: bool = False


class CompressedMemory(BaseModel):
    notes: list[str] = Field(default_factory=list)
    db_findings: list[str] = Field(default_factory=list)
    web_findings: list[str] = Field(default_factory=list)
    tool_summaries: list[str] = Field(default_factory=list)
    tool_outcomes: list[CompressedToolOutcome] = Field(default_factory=list)
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
    durable_memories: list[DurableMemoryRecord] = field(default_factory=list)
    retrieved_skills: list[DurableMemoryRecord] = field(default_factory=list)
    retrieved_source_packs: list[DurableMemoryRecord] = field(default_factory=list)
    memory_retrievals: list[MemoryRetrievalRecord] = field(default_factory=list)
    memory_mutations: list[MemoryMutationRecord] = field(default_factory=list)
    tool_summaries: list[str] = field(default_factory=list)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    db_mutations: list[DbMutationRecord] = field(default_factory=list)
    docs_lookups: list[DocumentationLookupRecord] = field(default_factory=list)
    compression_events: list[CompressionEvent] = field(default_factory=list)
    runtime_events: list[RuntimeEvent] = field(default_factory=list)
    web_tool_calls_used: int = 0
    web_tool_call_budget: int = 20
    compressed_memory: CompressedMemory | None = None
    pending_context_refresh_reason: str | None = None
    compression_notice: str | None = None
    compression_in_progress: bool = False
    last_compression_size: int = 0
    pending_model_switch: str | None = None
    memory_tool_calls_used: int = 0
    memory_tool_call_budget: int | None = None
    browser_session_started: bool = False
    db_checkpoint_path: str | None = None
    progress_hook: Any | None = None
    event_hook: Any | None = None

    def web_tool_budget(self) -> int:
        return self.web_tool_call_budget

    def web_tool_calls_remaining(self) -> int:
        return max(0, self.web_tool_budget() - self.web_tool_calls_used)

    def build_transfer_packet(self) -> ContextTransferPacket:
        if self.compressed_memory is not None:
            return ContextTransferPacket(
                mission_prompt=self.mission_request.prompt,
                notes=self.compressed_memory.notes,
                db_findings=self.compressed_memory.db_findings,
                web_findings=self.compressed_memory.web_findings,
                tool_summaries=self._build_tool_summary_block(),
                worked_tool_patterns=self._build_tool_pattern_block("worked"),
                failed_tool_patterns=self._build_tool_pattern_block("failed"),
                notice=self.compression_notice,
            )
        return ContextTransferPacket(
            mission_prompt=self.mission_request.prompt,
            notes=self.reasoning_notes[-20:],
            db_findings=self.db_findings[-20:],
            web_findings=self.web_findings[-20:],
            tool_summaries=self._build_tool_summary_block(),
            worked_tool_patterns=[],
            failed_tool_patterns=[],
            notice=self.compression_notice,
        )

    def estimate_working_memory_size(self) -> int:
        parts = [
            self.mission_request.prompt,
            self.build_learned_context_block(),
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
                    "\n".join(self._build_tool_pattern_block("worked")),
                    "\n".join(self._build_tool_pattern_block("failed")),
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

    def _build_tool_pattern_block(self, status: str) -> list[str]:
        if self.compressed_memory is None:
            return []
        items: list[str] = []
        for item in self.compressed_memory.tool_outcomes:
            if item.status != status:
                continue
            parts = [item.tool]
            if item.arguments_summary:
                parts.append(item.arguments_summary)
            if item.result_summary:
                parts.append(item.result_summary)
            elif item.error_message:
                parts.append(item.error_message)
            if item.retry_guidance:
                parts.append(item.retry_guidance)
            items.append(" | ".join(part for part in parts if part)[:400])
        return items

    def build_learned_context_block(self) -> str:
        groups = [("Relevant skills", self.retrieved_skills)]
        lines: list[str] = []
        for label, records in groups:
            if not records:
                continue
            lines.append(f"{label}:")
            for item in records:
                summary = f"- [{item.kind}] {item.title}: {item.body}"
                if item.tags:
                    summary += f" | tags: {', '.join(item.tags[:5])}"
                lines.append(summary[:500])
        return "\n".join(lines)


class ExecutionTrace(BaseModel):
    trace_id: str
    request: MissionRequest
    model_sequence: list[str]
    tool_calls: list[ToolCallRecord]
    db_mutations: list[DbMutationRecord]
    docs_lookups: list[DocumentationLookupRecord]
    web_artifacts: list[WebArtifact]
    memory_retrievals: list[MemoryRetrievalRecord] = Field(default_factory=list)
    memory_mutations: list[MemoryMutationRecord] = Field(default_factory=list)
    compression_events: list[CompressionEvent]
    runtime_events: list[RuntimeEvent]
    result: Any = None
    error: MissionError | None = None
    started_at: datetime
    completed_at: datetime


class SessionTurn(BaseModel):
    message_id: str
    role: str
    content: str
    created_at: datetime = Field(default_factory=utc_now)
    trace_id: str | None = None
    status: str = "completed"
    web_tool_call_limit_used: int | None = None


class SessionSummary(BaseModel):
    notes: list[str] = Field(default_factory=list)
    recent_tools: list[str] = Field(default_factory=list)
    compression_notice: str | None = None
    last_trace_id: str | None = None


class ResearchSession(BaseModel):
    session_id: str
    name: str
    normalized_name: str
    uses_dedicated_db: bool = False
    db_path: str
    preferred_model: str | None = None
    allowed_models: list[str] | None = None
    output_schema: dict[str, Any] | None = None
    web_enabled: bool = True
    db_mutation_enabled: bool = True
    web_tool_call_limit: int | None = Field(default=None, ge=0)
    turns: list[SessionTurn] = Field(default_factory=list)
    summary: SessionSummary = Field(default_factory=SessionSummary)
    last_error: MissionError | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
