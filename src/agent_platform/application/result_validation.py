from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel
from jsonschema import ValidationError, validate

from agent_platform.domain.enums import ResultFormat
from agent_platform.domain.exceptions import OutputValidationError


class ResultValidator:
    def validate(self, raw_output: Any, schema: dict[str, Any] | None) -> tuple[Any, ResultFormat]:
        if schema is None:
            return self._stringify(raw_output), ResultFormat.TEXT
        data = self._parse_json(raw_output)
        self._validate_schema(data, schema)
        return data, ResultFormat.JSON_SCHEMA

    def _parse_json(self, raw_output: Any) -> Any:
        if isinstance(raw_output, BaseModel):
            return raw_output.model_dump(mode="json")
        if isinstance(raw_output, (dict, list, int, float, bool)) or raw_output is None:
            return raw_output
        if not isinstance(raw_output, str):
            return raw_output
        try:
            return json.loads(raw_output)
        except json.JSONDecodeError as exc:
            raise OutputValidationError("agent did not return valid JSON") from exc

    def _validate_schema(self, data: Any, schema: dict[str, Any]) -> None:
        try:
            validate(instance=data, schema=schema)
        except ValidationError as exc:
            raise OutputValidationError(exc.message) from exc

    def _stringify(self, raw_output: Any) -> str:
        if isinstance(raw_output, str):
            return raw_output
        if isinstance(raw_output, BaseModel):
            return raw_output.model_dump_json()
        if isinstance(raw_output, (dict, list, int, float, bool)) or raw_output is None:
            return json.dumps(raw_output)
        return str(raw_output)
