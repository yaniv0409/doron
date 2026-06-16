from __future__ import annotations

from agent_platform.application.runtime_builder import MissionRuntime
from agent_platform.domain.models import DocumentationLookupRecord, ToolCallRecord


async def lookup_kuzu_docs(runtime: MissionRuntime, query: str) -> str:
    section = runtime.services.docs_repository.lookup(query)
    excerpt = section.body[:2_000]
    runtime.context.docs_lookups.append(
        DocumentationLookupRecord(
            query=query,
            source_id=section.source_id,
            excerpt=excerpt,
        )
    )
    runtime.context.tool_calls.append(
        ToolCallRecord(
            name="lookup_kuzu_docs",
            arguments={"query": query},
            result_summary=f"matched {section.source_id}",
        )
    )
    runtime.context.tool_summaries.append(f"lookup_kuzu_docs: {query}")
    return f"[{section.title}]\n{excerpt}"
