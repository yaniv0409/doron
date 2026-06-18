from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from agent_platform.agent.prompts import build_system_prompt
from agent_platform.application.live_events import emit_runtime_event
from agent_platform.application.output_schema import build_output_type
from agent_platform.application.runtime_builder import MissionRuntime
from agent_platform.domain.exceptions import ConfigurationError, ModelSwitchRequested
from agent_platform.domain.models import ToolResult
from agent_platform.tools.compression_tools import compress_context
from agent_platform.tools.db_tools import inspect_schema, read_graph, write_graph
from agent_platform.tools.docs_tools import lookup_kuzu_docs
from agent_platform.tools.memory_tools import deprecate_memory, read_memory, search_memory, update_memory, write_memory
from agent_platform.tools.model_tools import request_model_switch
from agent_platform.tools.trace_tools import grep_trace, read_trace_head
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
        agent_kwargs: dict[str, Any] = {
            "system_prompt": build_system_prompt(runtime.context),
            "deps_type": MissionRuntime,
        }
        output_type = build_output_type(runtime.context.mission_request.output_schema)
        if output_type is not None:
            agent_kwargs["output_type"] = output_type
        agent = Agent(model, **agent_kwargs)
        tool_names = self._register_tools(agent, runtime)
        return AgentSession(runtime=runtime, agent=agent, tool_names=tool_names)

    def _register_tools(self, agent: Any, runtime: MissionRuntime) -> list[str]:
        tool_names: list[str] = []
        is_memory_maintenance = runtime.context.mission_request.mission_metadata.get("mission_kind") == "memory_maintenance" if runtime.context.mission_request.mission_metadata else False

        @agent.tool
        async def graph_read(
            ctx: RunContext[MissionRuntime],
            query: str,
            reason: str,
            parameters: dict[str, Any] | None = None,
        ) -> ToolResult:
            arguments = {"query": query, "parameters": parameters or {}, "reason": reason}
            self._emit_tool_started(ctx.deps, "graph_read", arguments)
            result = await read_graph(ctx.deps, query, reason, parameters)
            self._emit_tool_completed(ctx.deps, "graph_read", arguments, result)
            return result
        tool_names.append("graph_read")

        if not runtime.services.settings.debug.disable_db_write_tool and not is_memory_maintenance:
            @agent.tool
            async def graph_write(
                ctx: RunContext[MissionRuntime],
                query: str,
                reason: str,
                parameters: dict[str, Any] | None = None,
            ) -> ToolResult:
                if not ctx.deps.context.mission_request.db_mutation_enabled:
                    raise ConfigurationError("database mutation is disabled for this mission")
                arguments = {"query": query, "parameters": parameters or {}, "reason": reason}
                self._emit_tool_started(ctx.deps, "graph_write", arguments)
                result = await write_graph(ctx.deps, query, reason, parameters)
                self._emit_tool_completed(ctx.deps, "graph_write", arguments, result)
                return result
            tool_names.append("graph_write")

        @agent.tool
        async def graph_schema(ctx: RunContext[MissionRuntime], reason: str) -> ToolResult:
            arguments: dict[str, Any] = {"reason": reason}
            self._emit_tool_started(ctx.deps, "graph_schema", arguments)
            result = await inspect_schema(ctx.deps, reason)
            self._emit_tool_completed(ctx.deps, "graph_schema", arguments, result)
            return result
        tool_names.append("graph_schema")

        @agent.tool
        async def kuzu_reference(ctx: RunContext[MissionRuntime], query: str, reason: str) -> ToolResult:
            arguments = {"query": query, "reason": reason}
            self._emit_tool_started(ctx.deps, "kuzu_reference", arguments)
            result = await lookup_kuzu_docs(ctx.deps, query, reason)
            self._emit_tool_completed(ctx.deps, "kuzu_reference", arguments, result)
            return result
        tool_names.append("kuzu_reference")

        if runtime.services.settings.memory.enabled:
            @agent.tool
            async def memory_search(ctx: RunContext[MissionRuntime], query: str, reason: str) -> ToolResult:
                arguments = {"query": query, "reason": reason}
                self._emit_tool_started(ctx.deps, "memory_search", arguments)
                result = await search_memory(ctx.deps, query, reason)
                self._emit_tool_completed(ctx.deps, "memory_search", arguments, result)
                return result
            tool_names.append("memory_search")

            @agent.tool
            async def memory_read(ctx: RunContext[MissionRuntime], ids: list[str], reason: str) -> ToolResult:
                arguments = {"ids": ids, "reason": reason}
                self._emit_tool_started(ctx.deps, "memory_read", arguments)
                result = await read_memory(ctx.deps, ids, reason)
                self._emit_tool_completed(ctx.deps, "memory_read", arguments, result)
                return result
            tool_names.append("memory_read")

            @agent.tool
            async def skill_search(ctx: RunContext[MissionRuntime], query: str, reason: str) -> ToolResult:
                arguments = {"query": query, "reason": reason}
                self._emit_tool_started(ctx.deps, "skill_search", arguments)
                result = await search_memory(ctx.deps, query, reason, kinds=["skill"], tool_name="skill_search")
                self._emit_tool_completed(ctx.deps, "skill_search", arguments, result)
                return result
            tool_names.append("skill_search")

            @agent.tool
            async def source_pack_search(ctx: RunContext[MissionRuntime], query: str, reason: str) -> ToolResult:
                arguments = {"query": query, "reason": reason}
                self._emit_tool_started(ctx.deps, "source_pack_search", arguments)
                result = await search_memory(
                    ctx.deps,
                    query,
                    reason,
                    kinds=["source_pack"],
                    tool_name="source_pack_search",
                )
                self._emit_tool_completed(ctx.deps, "source_pack_search", arguments, result)
                return result
            tool_names.append("source_pack_search")

            if is_memory_maintenance:
                @agent.tool
                async def trace_head(
                    ctx: RunContext[MissionRuntime],
                    reason: str,
                    chars: int | None = None,
                ) -> ToolResult:
                    arguments = {"reason": reason, "chars": chars}
                    self._emit_tool_started(ctx.deps, "trace_head", arguments)
                    result = await read_trace_head(ctx.deps, reason, chars)
                    self._emit_tool_completed(ctx.deps, "trace_head", arguments, result)
                    return result
                tool_names.append("trace_head")

                @agent.tool
                async def trace_grep(
                    ctx: RunContext[MissionRuntime],
                    pattern: str,
                    reason: str,
                    radius_lines: int | None = None,
                ) -> ToolResult:
                    arguments = {"pattern": pattern, "reason": reason, "radius_lines": radius_lines}
                    self._emit_tool_started(ctx.deps, "trace_grep", arguments)
                    result = await grep_trace(ctx.deps, pattern, reason, radius_lines)
                    self._emit_tool_completed(ctx.deps, "trace_grep", arguments, result)
                    return result
                tool_names.append("trace_grep")

                @agent.tool
                async def memory_write(
                    ctx: RunContext[MissionRuntime],
                    entries: list[dict[str, Any]],
                    reason: str,
                ) -> ToolResult:
                    arguments = {"entry_count": len(entries), "reason": reason}
                    self._emit_tool_started(ctx.deps, "memory_write", arguments)
                    result = await write_memory(ctx.deps, entries, reason)
                    self._emit_tool_completed(ctx.deps, "memory_write", arguments, result)
                    return result
                tool_names.append("memory_write")

                @agent.tool
                async def memory_update(
                    ctx: RunContext[MissionRuntime],
                    entries: list[dict[str, Any]],
                    reason: str,
                ) -> ToolResult:
                    arguments = {"entry_count": len(entries), "reason": reason}
                    self._emit_tool_started(ctx.deps, "memory_update", arguments)
                    result = await update_memory(ctx.deps, entries, reason)
                    self._emit_tool_completed(ctx.deps, "memory_update", arguments, result)
                    return result
                tool_names.append("memory_update")

                @agent.tool
                async def memory_deprecate(
                    ctx: RunContext[MissionRuntime],
                    ids: list[str],
                    reason: str,
                    replacement_id: str | None = None,
                ) -> ToolResult:
                    arguments = {"ids": ids, "replacement_id": replacement_id, "reason": reason}
                    self._emit_tool_started(ctx.deps, "memory_deprecate", arguments)
                    result = await deprecate_memory(ctx.deps, ids, reason, replacement_id)
                    self._emit_tool_completed(ctx.deps, "memory_deprecate", arguments, result)
                    return result
                tool_names.append("memory_deprecate")

        if not runtime.services.settings.debug.disable_browser_tools and not is_memory_maintenance:
            @agent.tool
            async def browser_open(ctx: RunContext[MissionRuntime], urls: list[str], reason: str) -> ToolResult:
                if not ctx.deps.context.mission_request.web_enabled:
                    raise ConfigurationError("web access is disabled for this mission")
                arguments = {"urls": urls, "reason": reason}
                self._emit_tool_started(ctx.deps, "browser_open", arguments)
                result = await open_url(ctx.deps, urls, reason)
                self._emit_tool_completed(ctx.deps, "browser_open", arguments, result)
                return result
            tool_names.append("browser_open")

            @agent.tool
            async def browser_text(ctx: RunContext[MissionRuntime], reason: str) -> ToolResult:
                if not ctx.deps.context.mission_request.web_enabled:
                    raise ConfigurationError("web access is disabled for this mission")
                arguments: dict[str, Any] = {"reason": reason}
                self._emit_tool_started(ctx.deps, "browser_text", arguments)
                result = await get_page_text(ctx.deps, reason)
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
