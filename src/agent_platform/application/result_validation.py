from __future__ import annotations

import json
from typing import Any

from jsonschema import ValidationError, validate

from agent_platform.domain.enums import ResultFormat
from agent_platform.domain.exceptions import OutputValidationError


class ResultValidator:
    def validate(self, raw_output: str, schema: dict[str, Any] | None) -> tuple[Any, ResultFormat]:
        if schema is None:
            return raw_output, ResultFormat.TEXT
        data = self._parse_json(raw_output)
        self._validate_schema(data, schema)
        return data, ResultFormat.JSON_SCHEMA

    def _parse_json(self, raw_output: str) -> Any:
        try:
            return json.loads(raw_output)
        except json.JSONDecodeError as exc:
            raise OutputValidationError("agent did not return valid JSON") from exc

    def _validate_schema(self, data: Any, schema: dict[str, Any]) -> None:
        try:
            validate(instance=data, schema=schema)
        except ValidationError as exc:
            raise OutputValidationError(exc.message) from exc
