from datetime import timezone

from agent_platform.contracts.serialization import to_api_response
from agent_platform.domain.enums import MissionStatus, ResultFormat
from agent_platform.domain.models import MissionResult, utc_now


def test_to_api_response_serializes_datetimes() -> None:
    now = utc_now().astimezone(timezone.utc)
    result = MissionResult(
        status=MissionStatus.COMPLETED,
        result={"ok": True},
        result_format=ResultFormat.JSON_SCHEMA,
        final_model="openai/gpt-5.2",
        trace_id="trace-1",
        started_at=now,
        completed_at=now,
    )
    response = to_api_response(result)
    assert response.trace_id == "trace-1"
    assert response.started_at.endswith("+00:00")
