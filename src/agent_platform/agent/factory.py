from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_platform.agent.prompts import build_system_prompt
from agent_platform.application.runtime_builder import MissionRuntime
from agent_platform.domain.exceptions import ConfigurationError
from agent_platform.domain.models import ToolResult
from agent_platform.tools.compression_tools import compress_context
from agent_platform.tools.db_tools import inspect_schema, read_graph, write_graph
from agent_platform.tools.docs_tools import lookup_kuzu_docs
from agent_platform.tools.model_tools import request_model_switch
from agent_platform.tools.web_tools import get_page_text, open_url

try:
    from pydantic_ai import Agent, RunContext
    from pydantic_ai.models.openrouter import OpenRouterModel
    from pydantic_ai.providers.openrouter import OpenRouterProvider
except ImportError:  # pragma: no cover
    Agent = None
    RunContext = Any
    OpenRouterModel = None
    OpenRouterProvider = None


@dataclass(slots=True)
class AgentSession:
    runtime: MissionRuntime
    agent: Any


class AgentFactory:
    def create(self, runtime: MissionRuntime) -> AgentSession:
        if Agent is None or OpenRouterModel is None or OpenRouterProvider is None:
            raise ConfigurationError("pydantic-ai openrouter support is not installed")
        api_key = runtime.services.settings.openrouter.api_key
        if not api_key:
            raise ConfigurationError("OPENROUTER_API_KEY is not configured")
        provider = OpenRouterProvider(
            api_key=api_key,
            app_url=runtime.services.settings.openrouter.app_url,
            app_title=runtime.services.settings.openrouter.app_title,
        )
        model = OpenRouterModel(runtime.context.current_model.name, provider=provider)
        agent = Agent(
            model,
            system_prompt=build_system_prompt(runtime.context),
            deps_type=MissionRuntime,
        )
        self._register_tools(agent, runtime)
        return AgentSession(runtime=runtime, agent=agent)

    def _register_tools(self, agent: Any, runtime: MissionRuntime) -> None:
        @agent.tool
        async def graph_read(
            ctx: RunContext[MissionRuntime],
            query: str,
            parameters: dict[str, Any] | None = None,
        ) -> ToolResult:
            return await read_graph(ctx.deps, query, parameters)

        @agent.tool
        async def graph_write(
            ctx: RunContext[MissionRuntime],
            query: str,
            parameters: dict[str, Any] | None = None,
        ) -> ToolResult:
            if not ctx.deps.context.mission_request.db_mutation_enabled:
                raise ConfigurationError("database mutation is disabled for this mission")
            return await write_graph(ctx.deps, query, parameters)

        @agent.tool
        async def graph_schema(ctx: RunContext[MissionRuntime]) -> ToolResult:
            return await inspect_schema(ctx.deps)

        @agent.tool
        async def kuzu_reference(ctx: RunContext[MissionRuntime], query: str) -> ToolResult:
            return await lookup_kuzu_docs(ctx.deps, query)

        @agent.tool
        async def browser_open(ctx: RunContext[MissionRuntime], url: str) -> ToolResult:
            if not ctx.deps.context.mission_request.web_enabled:
                raise ConfigurationError("web access is disabled for this mission")
            return await open_url(ctx.deps, url)

        @agent.tool
        async def browser_text(ctx: RunContext[MissionRuntime]) -> ToolResult:
            if not ctx.deps.context.mission_request.web_enabled:
                raise ConfigurationError("web access is disabled for this mission")
            return await get_page_text(ctx.deps)

        @agent.tool
        async def switch_model(
            ctx: RunContext[MissionRuntime],
            target_model: str,
            reason: str,
        ) -> str:
            return await request_model_switch(ctx.deps, target_model, reason)

        @agent.tool
        async def clean_context(
            ctx: RunContext[MissionRuntime],
            reason: str,
        ) -> ToolResult:
            return await compress_context(ctx.deps, reason)

        runtime.context.tool_summaries.append(
            "registered tools: graph_read, graph_write, graph_schema, kuzu_reference, browser_open, browser_text, switch_model, clean_context",
        )
