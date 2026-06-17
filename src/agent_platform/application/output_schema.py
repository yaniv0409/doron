from __future__ import annotations

from typing import Any

from pydantic_ai import StructuredDict

from agent_platform.domain.exceptions import ConfigurationError


def build_output_type(schema: dict[str, Any] | None) -> type[Any] | None:
    if schema is None:
        return None
    _validate_object_schema(schema)
    return StructuredDict(schema, name="MissionOutput")


def _validate_object_schema(schema: dict[str, Any]) -> None:
    schema_type = schema.get("type")
    if schema_type not in {None, "object"} and "properties" not in schema:
        raise ConfigurationError("output schema must be an object schema to use structured output")

    properties = schema.get("properties") or {}
    if not isinstance(properties, dict):
        raise ConfigurationError("output schema properties must be an object")
    if not isinstance(schema.get("required", []), list):
        raise ConfigurationError("output schema required must be a list")
