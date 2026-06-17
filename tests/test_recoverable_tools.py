import asyncio
from types import SimpleNamespace

from agent_platform.agent.prompts import build_system_prompt
from agent_platform.domain.exceptions import DatabaseError, DocumentationError
from agent_platform.domain.models import MissionRequest, ModelDescriptor, RuntimeContext, utc_now
from agent_platform.tools.db_tools import inspect_schema, read_graph
from agent_platform.tools.docs_tools import lookup_kuzu_docs


class FailingDb:
    def execute(self, query: str, parameters=None):
        raise DatabaseError("Binder exception: Table Company does not exist.")

    def get_schema(self) -> str:
        raise DatabaseError("Binder exception: Table Company does not exist.")


class FailingDocsRepository:
    def lookup(self, query: str):
        raise DocumentationError(f"no documentation match for: {query}")


def build_runtime() -> SimpleNamespace:
    request = MissionRequest(prompt="what are elon's companies?", db_path="/tmp/demo.kuzu")
    context = RuntimeContext(
        trace_id="trace-1",
        mission_request=request,
        started_at=utc_now(),
        current_model=ModelDescriptor(name="openai/gpt-4.1-mini", rank=10),
        allowed_models=[ModelDescriptor(name="openai/gpt-4.1-mini", rank=10)],
    )
    services = SimpleNamespace(
        docs_repository=FailingDocsRepository(),
        trace_store=SimpleNamespace(create_checkpoint=lambda *_args, **_kwargs: "/tmp/checkpoint"),
    )
    return SimpleNamespace(context=context, db=FailingDb(), services=services)


def test_read_graph_returns_recoverable_error_result() -> None:
    runtime = build_runtime()

    result = asyncio.run(read_graph(runtime, "MATCH (c:Company) RETURN c", "find Elon-related companies"))

    assert result.ok is False
    assert result.tool == "read_graph"
    assert result.error_type == "database_missing_object"
    assert "Inspect the schema first" in result.retry_hint
    assert runtime.context.tool_calls[0].ok is False


def test_inspect_schema_returns_recoverable_error_result() -> None:
    runtime = build_runtime()

    result = asyncio.run(inspect_schema(runtime, "check available graph tables"))

    assert result.ok is False
    assert result.tool == "inspect_schema"
    assert result.error_type == "database_missing_object"


def test_lookup_kuzu_docs_returns_recoverable_error_result() -> None:
    runtime = build_runtime()

    result = asyncio.run(lookup_kuzu_docs(runtime, "companies", "understand Kuzu table names"))

    assert result.ok is False
    assert result.tool == "lookup_kuzu_docs"
    assert result.error_type == "docs_lookup_error"


def test_system_prompt_instructs_agent_to_continue_after_tool_failure() -> None:
    runtime = build_runtime()

    prompt = build_system_prompt(runtime.context)

    assert "ok=false" in prompt
    assert "do not give up" in prompt
    assert "compress_context" in prompt
