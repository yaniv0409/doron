import asyncio
from types import SimpleNamespace

from agent_platform.agent.prompts import build_system_prompt
from agent_platform.config.settings import AppSettings
from agent_platform.domain.exceptions import DatabaseError, DocumentationError
from agent_platform.domain.models import MissionRequest, ModelDescriptor, RuntimeContext, utc_now
from agent_platform.tools import web_tools
from agent_platform.tools.db_tools import inspect_schema, read_graph, write_graph
from agent_platform.tools.docs_tools import lookup_kuzu_docs
from agent_platform.tools.web_tools import get_page_text, open_url, web_search


class FailingDb:
    def execute(self, query: str, parameters=None):
        raise DatabaseError("Binder exception: Table Company does not exist.")

    def get_schema(self) -> str:
        raise DatabaseError("Binder exception: Table Company does not exist.")


class WritableDb:
    def __init__(self) -> None:
        self.execute_calls: list[tuple[str, dict | None]] = []
        self.sync_calls = 0

    def execute(self, query: str, parameters=None):
        self.execute_calls.append((query, parameters))
        return [{"query": query}]

    def sync(self) -> None:
        self.sync_calls += 1


class FailingDocsRepository:
    def lookup(self, query: str):
        raise DocumentationError(f"no documentation match for: {query}")


def build_runtime() -> SimpleNamespace:
    request = MissionRequest(
        prompt="what are elon's companies?",
        memory_db_path="/tmp/demo/memory.kuzu",
        research_meta_db_path="/tmp/demo/research_meta.kuzu",
    )
    context = RuntimeContext(
        trace_id="trace-1",
        mission_request=request,
        started_at=utc_now(),
        current_model=ModelDescriptor(name="openai/gpt-4.1-mini", rank=10),
        allowed_models=[ModelDescriptor(name="openai/gpt-4.1-mini", rank=10)],
    )
    services = SimpleNamespace(
        settings=AppSettings(),
        context_compressor=SimpleNamespace(
            should_auto_compress=lambda _runtime: False,
            compress=None,
        ),
        docs_repository=FailingDocsRepository(),
        trace_store=SimpleNamespace(create_checkpoint=lambda *_args, **_kwargs: "/tmp/checkpoint"),
    )
    return SimpleNamespace(context=context, memory_db=FailingDb(), research_meta_db=FailingDb(), services=services)


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


def test_write_graph_syncs_after_successful_write() -> None:
    runtime = build_runtime()
    runtime.memory_db = WritableDb()

    result = asyncio.run(
        write_graph(
            runtime,
            "CREATE (c:Company {ticker: 'TEST'})",
            "persist a test company",
        )
    )

    assert result.ok is True
    assert runtime.memory_db.sync_calls == 1
    assert runtime.context.db_mutations[0].query == "CREATE (c:Company {ticker: 'TEST'})"


def test_lookup_kuzu_docs_returns_recoverable_error_result() -> None:
    runtime = build_runtime()

    result = asyncio.run(lookup_kuzu_docs(runtime, "companies", "understand Kuzu table names"))

    assert result.ok is False
    assert result.tool == "lookup_kuzu_docs"
    assert result.error_type == "docs_lookup_error"


def test_read_graph_returns_runtime_error_result() -> None:
    class RuntimeFailingDb(FailingDb):
        def execute(self, query: str, parameters=None):
            raise RuntimeError("database driver exploded")

    runtime = build_runtime()
    runtime.memory_db = RuntimeFailingDb()

    result = asyncio.run(read_graph(runtime, "MATCH (c:Company) RETURN c", "find Elon-related companies"))

    assert result.ok is False
    assert result.error_type == "database_runtime_error"


def test_lookup_kuzu_docs_returns_runtime_error_result() -> None:
    class RuntimeFailingDocsRepository(FailingDocsRepository):
        def lookup(self, query: str):
            raise RuntimeError("docs parser exploded")

    runtime = build_runtime()
    runtime.services.docs_repository = RuntimeFailingDocsRepository()

    result = asyncio.run(lookup_kuzu_docs(runtime, "companies", "understand Kuzu table names"))

    assert result.ok is False
    assert result.error_type == "docs_runtime_error"


def test_get_page_text_returns_runtime_error_result() -> None:
    class RuntimeFailingBrowser:
        async def extract_text(self):
            raise RuntimeError("browser extractor exploded")

    runtime = build_runtime()
    runtime.browser = RuntimeFailingBrowser()

    result = asyncio.run(get_page_text(runtime, "inspect current page"))

    assert result.ok is False
    assert result.error_type == "browser_runtime_error"


def test_open_url_returns_runtime_error_result(monkeypatch) -> None:
    runtime = build_runtime()

    def fail_batch_sync(*args, **kwargs):
        raise RuntimeError("batch fetch exploded")

    monkeypatch.setattr(web_tools, "_fetch_batch_sync", fail_batch_sync)

    result = asyncio.run(open_url(runtime, ["https://example.com"], "search the web"))

    assert result.ok is False
    assert result.error_type == "browser_runtime_error"


def test_web_search_returns_normalized_result(monkeypatch) -> None:
    runtime = build_runtime()

    def fake_duckduckgo_search(query: str, max_results: int | None):
        assert query == "elon musk companies"
        assert max_results == 5
        return [
            {"title": "Tesla", "href": "https://tesla.com", "body": "Electric vehicles."},
            {"title": "SpaceX", "href": "https://spacex.com", "body": "Spaceflight company."},
        ]

    monkeypatch.setattr(web_tools, "_duckduckgo_search", fake_duckduckgo_search)

    result = asyncio.run(web_search(runtime, "elon musk companies", "find likely sources"))

    assert result.ok is True
    assert result.tool == "web_search"
    assert result.data["query"] == "elon musk companies"
    assert result.data["source"] == "duckduckgo"
    assert [hit["url"] for hit in result.data["hits"]] == ["https://tesla.com", "https://spacex.com"]
    assert runtime.context.tool_calls[0].name == "web_search"
    assert runtime.context.tool_calls[0].result_summary == "returned 2 web result(s)"


def test_system_prompt_instructs_agent_to_continue_after_tool_failure() -> None:
    runtime = build_runtime()

    prompt = build_system_prompt(runtime.context)

    assert "ok=false" in prompt
    assert "do not give up" in prompt
    assert "compress_context" in prompt
    assert "web_search" in prompt
