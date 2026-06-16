from typing import Any

from pydantic import BaseModel, Field

from agent_platform.domain.enums import MissionStatus, ResultFormat


class MissionRunRequest(BaseModel):
    prompt: str = Field(min_length=1)
    db_path: str = Field(min_length=1)
    output_schema: dict[str, Any] | None = None
    preferred_model: str | None = None
    allowed_models: list[str] | None = None
    mission_metadata: dict[str, Any] | None = None
    web_enabled: bool = True
    db_mutation_enabled: bool = True


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
    error: MissionRunError | None = None
