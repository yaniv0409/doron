from agent_platform.contracts.api import MissionRunError, MissionRunResponse
from agent_platform.domain.models import MissionResult


def to_api_response(result: MissionResult) -> MissionRunResponse:
    error = None
    if result.error:
        error = MissionRunError(
            code=result.error.code,
            message=result.error.message,
            details=result.error.details,
        )
    return MissionRunResponse(
        status=result.status,
        result=result.result,
        result_format=result.result_format,
        final_model=result.final_model,
        trace_id=result.trace_id,
        started_at=result.started_at.isoformat(),
        completed_at=result.completed_at.isoformat(),
        error=error,
    )
