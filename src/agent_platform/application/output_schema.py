from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, create_model

from agent_platform.domain.exceptions import ConfigurationError


def build_output_type(schema: dict[str, Any] | None) -> type[BaseModel] | None:
    if schema is None:
        return None
    return _schema_to_model(schema, "MissionOutput")


def _schema_to_model(schema: dict[str, Any], model_name: str) -> type[BaseModel]:
    schema_type = schema.get("type")
    if schema_type not in {None, "object"} and "properties" not in schema:
        raise ConfigurationError("output schema must be an object schema to use structured output")

    properties = schema.get("properties") or {}
    if not isinstance(properties, Mapping):
        raise ConfigurationError("output schema properties must be an object")

    required = set(schema.get("required") or [])
    allow_extra = schema.get("additionalProperties", True) is not False
    field_definitions: dict[str, tuple[Any, Any]] = {}
    for name, subschema in properties.items():
        if not isinstance(subschema, Mapping):
            raise ConfigurationError(f"output schema property {name!r} must be a schema object")
        annotation = _schema_to_annotation(dict(subschema), f"{model_name}_{_sanitize_name(name)}")
        if name not in required:
            annotation = annotation | None
            default = None
        else:
            default = ...
        field_definitions[name] = (annotation, default)

    config = ConfigDict(extra="allow" if allow_extra else "forbid")
    return create_model(model_name, __config__=config, **field_definitions)


def _schema_to_annotation(schema: dict[str, Any], model_name: str) -> Any:
    if "enum" in schema:
        enum_values = schema["enum"]
        if not isinstance(enum_values, list) or not enum_values:
            raise ConfigurationError("output schema enum must be a non-empty list")
        return Literal.__getitem__(tuple(enum_values))

    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        non_null = [item for item in schema_type if item != "null"]
        if len(non_null) != 1 or len(non_null) != len(schema_type) - 1:
            raise ConfigurationError("union output schemas are only supported for nullable single types")
        return _schema_to_annotation({**schema, "type": non_null[0]}, model_name) | None

    if schema_type == "string":
        return str
    if schema_type == "integer":
        return int
    if schema_type == "number":
        return float
    if schema_type == "boolean":
        return bool
    if schema_type == "null":
        return type(None)
    if schema_type == "array":
        items = schema.get("items")
        if not isinstance(items, Mapping):
            raise ConfigurationError("array output schemas must define items")
        return list[_schema_to_annotation(dict(items), f"{model_name}_Item")]
    if schema_type == "object" or "properties" in schema:
        return _schema_to_model(schema, model_name)
    if schema_type is None:
        return Any
    raise ConfigurationError(f"unsupported output schema type: {schema_type!r}")


def _sanitize_name(value: str) -> str:
    chars = [ch if ch.isalnum() else "_" for ch in value]
    sanitized = "".join(chars).strip("_")
    return sanitized or "field"
