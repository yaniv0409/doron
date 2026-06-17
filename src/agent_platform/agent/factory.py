from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from agent_platform.agent.prompts import build_system_prompt
from agent_platform.application.live_events import emit_runtime_event
from agent_platform.application.runtime_builder import MissionRuntime
from agent_platform.domain.exceptions import ConfigurationError, ModelSwitchRequested
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
    tool_names: list[str]


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
        tool_names = self._register_tools(agent, runtime)
        return AgentSession(runtime=runtime, agent=agent, tool_names=tool_names)

    def _register_tools(self, agent: Any, runtime: MissionRuntime) -> list[str]:
        tool_names: list[str] = []

        @agent.tool
        async def graph_read(
            ctx: RunContext[MissionRuntime],
            query: str,
            parameters: dict[str, Any] | None = None,
        ) -> ToolResult:
            arguments = {"query": query, "parameters": parameters or {}}
            self._emit_tool_started(ctx.deps, "graph_read", arguments)
            result = await read_graph(ctx.deps, query, parameters)
            self._emit_tool_completed(ctx.deps, "graph_read", arguments, result)
            return result
        tool_names.append("graph_read")

        if not runtime.services.settings.debug.disable_db_write_tool:
            @agent.tool
            async def graph_write(
                ctx: RunContext[MissionRuntime],
                query: str,
                parameters: dict[str, Any] | None = None,
            ) -> ToolResult:
                if not ctx.deps.context.mission_request.db_mutation_enabled:
                    raise ConfigurationError("database mutation is disabled for this mission")
                arguments = {"query": query, "parameters": parameters or {}}
                self._emit_tool_started(ctx.deps, "graph_write", arguments)
                result = await write_graph(ctx.deps, query, parameters)
                self._emit_tool_completed(ctx.deps, "graph_write", arguments, result)
                return result
            tool_names.append("graph_write")

        @agent.tool
        async def graph_schema(ctx: RunContext[MissionRuntime]) -> ToolResult:
            arguments: dict[str, Any] = {}
            self._emit_tool_started(ctx.deps, "graph_schema", arguments)
            result = await inspect_schema(ctx.deps)
            self._emit_tool_completed(ctx.deps, "graph_schema", arguments, result)
            return result
        tool_names.append("graph_schema")

        @agent.tool
        async def kuzu_reference(ctx: RunContext[MissionRuntime], query: str) -> ToolResult:
            arguments = {"query": query}
            self._emit_tool_started(ctx.deps, "kuzu_reference", arguments)
            result = await lookup_kuzu_docs(ctx.deps, query)
            self._emit_tool_completed(ctx.deps, "kuzu_reference", arguments, result)
            return result
        tool_names.append("kuzu_reference")

        if not runtime.services.settings.debug.disable_browser_tools:
            @agent.tool
            async def browser_open(ctx: RunContext[MissionRuntime], url: str) -> ToolResult:
                if not ctx.deps.context.mission_request.web_enabled:
                    raise ConfigurationError("web access is disabled for this mission")
                arguments = {"url": url}
                self._emit_tool_started(ctx.deps, "browser_open", arguments)
                result = await open_url(ctx.deps, url)
                self._emit_tool_completed(ctx.deps, "browser_open", arguments, result)
                return result
            tool_names.append("browser_open")

            @agent.tool
            async def browser_text(ctx: RunContext[MissionRuntime]) -> ToolResult:
                if not ctx.deps.context.mission_request.web_enabled:
                    raise ConfigurationError("web access is disabled for this mission")
                arguments: dict[str, Any] = {}
                self._emit_tool_started(ctx.deps, "browser_text", arguments)
                result = await get_page_text(ctx.deps)
                self._emit_tool_completed(ctx.deps, "browser_text", arguments, result)
                return result
            tool_names.append("browser_text")

        if not runtime.services.settings.debug.disable_model_switch_tool:
            @agent.tool
            async def switch_model(
                ctx: RunContext[MissionRuntime],
                target_model: str,
                reason: str,
            ) -> str:
                arguments = {"target_model": target_model, "reason": reason}
                self._emit_tool_started(ctx.deps, "switch_model", arguments)
                try:
                    result = await request_model_switch(ctx.deps, target_model, reason)
                except ModelSwitchRequested as exc:
                    self._emit_tool_completed(
                        ctx.deps,
                        "switch_model",
                        arguments,
                        f"requested switch to {target_model}",
                    )
                    raise
                except Exception as exc:
                    self._emit_tool_completed(
                        ctx.deps,
                        "switch_model",
                        arguments,
                        None,
                        ok=False,
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                    )
                    raise
                self._emit_tool_completed(ctx.deps, "switch_model", arguments, result)
                return result
            tool_names.append("switch_model")

        if runtime.services.settings.compression.tool_enabled and not runtime.services.settings.debug.disable_compression_tool:
            @agent.tool
            async def clean_context(
                ctx: RunContext[MissionRuntime],
                reason: str,
            ) -> ToolResult:
                arguments = {"reason": reason}
                self._emit_tool_started(ctx.deps, "clean_context", arguments)
                result = await compress_context(ctx.deps, reason)
                self._emit_tool_completed(ctx.deps, "clean_context", arguments, result)
                return result
            tool_names.append("clean_context")

        runtime.context.tool_summaries.append(
            f"registered tools: {', '.join(tool_names)}",
        )
        return tool_names

    def _emit_tool_started(self, runtime: MissionRuntime, name: str, arguments: dict[str, Any]) -> None:
        emit_runtime_event(
            runtime.context,
            "tool.started",
            f"{name} started",
            {"name": name, "arguments": arguments},
            stream_event="tool.started",
        )

    def _emit_tool_completed(
        self,
        runtime: MissionRuntime,
        name: str,
        arguments: dict[str, Any],
        result: ToolResult | str | None,
        *,
        ok: bool | None = None,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> None:
        result_ok = ok
        result_summary = ""
        if isinstance(result, ToolResult):
            result_ok = result.ok if result_ok is None else result_ok
            error_type = error_type or result.error_type
            error_message = error_message or result.error_message
            if result.ok:
                result_summary = self._summarize_value(result.data)
            else:
                result_summary = result.retry_hint or result.error_message or name
        elif isinstance(result, str):
            result_summary = result
            result_ok = True if result_ok is None else result_ok
        else:
            result_summary = name
            result_ok = True if result_ok is None else result_ok
        emit_runtime_event(
            runtime.context,
            "tool.completed",
            f"{name} completed",
            {
                "name": name,
                "arguments": arguments,
                "ok": result_ok,
                "result_summary": result_summary,
                "error_type": error_type,
                "error_message": error_message,
            },
            stream_event="tool.completed",
        )

    def _summarize_value(self, value: Any) -> str:
        if value is None:
            return "completed"
        if isinstance(value, str):
            return value[:240]
        try:
            rendered = json.dumps(value, ensure_ascii=False)
        except TypeError:
            rendered = repr(value)
        return rendered[:240]
