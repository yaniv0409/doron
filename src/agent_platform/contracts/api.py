from typing import Any

from pydantic import BaseModel, Field

from agent_platform.domain.enums import MissionStatus, ResultFormat
from agent_platform.domain.models import CompletionMetadata


class MissionRunRequest(BaseModel):
    prompt: str = Field(min_length=1)
    memory_db_path: str = Field(min_length=1)
    research_meta_db_path: str = Field(min_length=1)
    output_schema: dict[str, Any] | None = None
    preferred_model: str | None = None
    allowed_models: list[str] | None = None
    mission_metadata: dict[str, Any] | None = None
    web_enabled: bool = True
    db_mutation_enabled: bool = True
    web_tool_call_limit: int | None = Field(default=None, ge=0)
    stream: bool = False


class MissionRunError(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class MissionRunResponse(BaseModel):
    status: MissionStatus
    result: Any = None
    result_format: ResultFormat
    final_model: str
    trace_id: str
    started_at: str
    completed_at: str
    completion: CompletionMetadata | None = None
    error: MissionRunError | None = None


class MissionStreamEvent(BaseModel):
    event: str
    data: dict[str, Any]
