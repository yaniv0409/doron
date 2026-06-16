import pytest

from agent_platform.application.result_validation import ResultValidator
from agent_platform.domain.enums import ResultFormat
from agent_platform.domain.exceptions import OutputValidationError


def test_validate_plain_text() -> None:
    validator = ResultValidator()
    result, result_format = validator.validate("hello", None)
    assert result == "hello"
    assert result_format is ResultFormat.TEXT


def test_validate_json_schema_success() -> None:
    validator = ResultValidator()
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
    }
    result, result_format = validator.validate('{"answer":"ok"}', schema)
    assert result == {"answer": "ok"}
    assert result_format is ResultFormat.JSON_SCHEMA


def test_validate_json_schema_failure() -> None:
    validator = ResultValidator()
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
    }
    with pytest.raises(OutputValidationError):
        validator.validate('{"wrong":"field"}', schema)
