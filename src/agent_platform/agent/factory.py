from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any

from agent_platform.agent.prompts import build_system_prompt
from agent_platform.application.live_events import emit_runtime_event
from agent_platform.application.output_schema import build_output_type
from agent_platform.application.runtime_builder import MissionRuntime
from agent_platform.domain.exceptions import ConfigurationError, ModelSwitchRequested
from agent_platform.domain.models import ToolResult
from agent_platform.tools.db_tools import inspect_schema as run_inspect_schema
from agent_platform.tools.db_tools import read_graph as run_read_graph
from agent_platform.tools.db_tools import write_graph as run_write_graph
from agent_platform.tools.compression_tools import compress_context
from agent_platform.tools.memory_tools import (
    skill_deprecate as run_skill_deprecate,
    skill_read as run_skill_read,
    skill_search as run_skill_search,
    skill_update as run_skill_update,
    skill_write as run_skill_write,
)
from agent_platform.tools.research_tools import (
    advance_existing as run_advance_existing,
    advance_new as run_advance_new,
    create_root_work_item as run_create_root_work_item,
    get_ancestry as run_get_ancestry,
    get_descendants as run_get_descendants,
    get_frontier as run_get_frontier,
    search_research_nodes as run_search_research_nodes,
)
from agent_platform.tools.model_tools import request_model_switch
from agent_platform.tools.web_tools import get_page_text, open_url, web_search as run_web_search

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
        is_skill_maintenance = runtime.context.mission_request.mission_metadata.get("mission_kind") == "skill_maintenance" if runtime.context.mission_request.mission_metadata else False
        if runtime.context.mission_request.web_enabled:
            tool_names.append("web_search")

            @agent.tool
            async def web_search(ctx: RunContext[MissionRuntime], query: str, reason: str) -> ToolResult:
                """Search the public web for a query and return a compact list of results."""
                arguments = {"query": query, "reason": reason}
                self._emit_tool_started(ctx.deps, "web_search", arguments)
                result = await run_web_search(ctx.deps, query, reason)
                self._emit_tool_completed(ctx.deps, "web_search", arguments, result)
                return result

        if runtime.services.settings.memory.enabled:
            @agent.tool
            async def skill_search(ctx: RunContext[MissionRuntime], query: str, reason: str) -> ToolResult:
                arguments = {"query": query, "reason": reason}
                self._emit_tool_started(ctx.deps, "skill_search", arguments)
                result = await run_skill_search(ctx.deps, query, reason)
                self._emit_tool_completed(ctx.deps, "skill_search", arguments, result)
                return result

            tool_names.append("skill_search")

        if not is_skill_maintenance:
            @agent.tool
            async def create_root_work_item(
                ctx: RunContext[MissionRuntime],
                prompt: str,
                reason: str,
            ) -> ToolResult:
                arguments = {"prompt": prompt, "reason": reason}
                self._emit_tool_started(ctx.deps, "create_root_work_item", arguments)
                result = await run_create_root_work_item(ctx.deps, prompt, reason)
                self._emit_tool_completed(ctx.deps, "create_root_work_item", arguments, result)
                return result

            tool_names.append("create_root_work_item")

            @agent.tool
            async def advance_new(
                ctx: RunContext[MissionRuntime],
                from_node_id: str,
                edge: dict[str, Any],
                new_node: dict[str, Any],
                reason: str,
            ) -> ToolResult:
                arguments = {
                    "from_node_id": from_node_id,
                    "edge": edge,
                    "new_node": new_node,
                    "reason": reason,
                }
                self._emit_tool_started(ctx.deps, "advance_new", arguments)
                result = await run_advance_new(ctx.deps, from_node_id, edge, new_node, reason)
                self._emit_tool_completed(ctx.deps, "advance_new", arguments, result)
                return result

            tool_names.append("advance_new")

            @agent.tool
            async def advance_existing(
                ctx: RunContext[MissionRuntime],
                from_node_id: str,
                to_node_id: str,
                edge: dict[str, Any],
                reason: str,
            ) -> ToolResult:
                arguments = {
                    "from_node_id": from_node_id,
                    "to_node_id": to_node_id,
                    "edge": edge,
                    "reason": reason,
                }
                self._emit_tool_started(ctx.deps, "advance_existing", arguments)
                result = await run_advance_existing(ctx.deps, from_node_id, to_node_id, edge, reason)
                self._emit_tool_completed(ctx.deps, "advance_existing", arguments, result)
                return result

            tool_names.append("advance_existing")

            @agent.tool
            async def get_frontier(ctx: RunContext[MissionRuntime], reason: str) -> ToolResult:
                arguments = {"reason": reason}
                self._emit_tool_started(ctx.deps, "get_frontier", arguments)
                result = await run_get_frontier(ctx.deps, reason)
                self._emit_tool_completed(ctx.deps, "get_frontier", arguments, result)
                return result

            tool_names.append("get_frontier")

            @agent.tool
            async def get_ancestry(
                ctx: RunContext[MissionRuntime],
                node_id: str,
                depth: int,
                reason: str,
            ) -> ToolResult:
                arguments = {"node_id": node_id, "depth": depth, "reason": reason}
                self._emit_tool_started(ctx.deps, "get_ancestry", arguments)
                result = await run_get_ancestry(ctx.deps, node_id, depth, reason)
                self._emit_tool_completed(ctx.deps, "get_ancestry", arguments, result)
                return result

            tool_names.append("get_ancestry")

            @agent.tool
            async def get_descendants(
                ctx: RunContext[MissionRuntime],
                node_id: str,
                depth: int,
                mode: str,
                reason: str,
            ) -> ToolResult:
                arguments = {"node_id": node_id, "depth": depth, "mode": mode, "reason": reason}
                self._emit_tool_started(ctx.deps, "get_descendants", arguments)
                result = await run_get_descendants(ctx.deps, node_id, depth, mode, reason)
                self._emit_tool_completed(ctx.deps, "get_descendants", arguments, result)
                return result

            tool_names.append("get_descendants")

            @agent.tool
            async def search_research_nodes(
                ctx: RunContext[MissionRuntime],
                query: str,
                reason: str,
                limit: int = 8,
                include_failures: bool = False,
            ) -> ToolResult:
                arguments = {
                    "query": query,
                    "limit": limit,
                    "include_failures": include_failures,
                    "reason": reason,
                }
                self._emit_tool_started(ctx.deps, "search_research_nodes", arguments)
                result = await run_search_research_nodes(ctx.deps, query, limit, include_failures, reason)
                self._emit_tool_completed(ctx.deps, "search_research_nodes", arguments, result)
                return result

            tool_names.append("search_research_nodes")

            @agent.tool
            async def read_graph(
                ctx: RunContext[MissionRuntime],
                query: str,
                reason: str,
                parameters: dict[str, Any] | None = None,
            ) -> ToolResult:
                arguments = {"query": query, "parameters": parameters or {}, "reason": reason}
                self._emit_tool_started(ctx.deps, "read_graph", arguments)
                result = await run_read_graph(ctx.deps, query, reason, parameters)
                self._emit_tool_completed(ctx.deps, "read_graph", arguments, result)
                return result

            tool_names.append("read_graph")

            @agent.tool
            async def write_graph(
                ctx: RunContext[MissionRuntime],
                query: str,
                reason: str,
                parameters: dict[str, Any] | None = None,
            ) -> ToolResult:
                if not ctx.deps.context.mission_request.db_mutation_enabled:
                    raise ConfigurationError("graph mutations are disabled for this mission")
                arguments = {"query": query, "parameters": parameters or {}, "reason": reason}
                self._emit_tool_started(ctx.deps, "write_graph", arguments)
                result = await run_write_graph(ctx.deps, query, reason, parameters)
                self._emit_tool_completed(ctx.deps, "write_graph", arguments, result)
                return result

            tool_names.append("write_graph")

            @agent.tool
            async def inspect_schema(ctx: RunContext[MissionRuntime], reason: str) -> ToolResult:
                arguments = {"reason": reason}
                self._emit_tool_started(ctx.deps, "inspect_schema", arguments)
                result = await run_inspect_schema(ctx.deps, reason)
                self._emit_tool_completed(ctx.deps, "inspect_schema", arguments, result)
                return result

            tool_names.append("inspect_schema")

        if is_skill_maintenance:
            @agent.tool
            async def read_graph(
                ctx: RunContext[MissionRuntime],
                query: str,
                reason: str,
                parameters: dict[str, Any] | None = None,
            ) -> ToolResult:
                arguments = {"query": query, "parameters": parameters or {}, "reason": reason}
                self._emit_tool_started(ctx.deps, "read_graph", arguments)
                result = await run_read_graph(ctx.deps, query, reason, parameters)
                self._emit_tool_completed(ctx.deps, "read_graph", arguments, result)
                return result

            tool_names.append("read_graph")

            @agent.tool
            async def inspect_schema(ctx: RunContext[MissionRuntime], reason: str) -> ToolResult:
                arguments = {"reason": reason}
                self._emit_tool_started(ctx.deps, "inspect_schema", arguments)
                result = await run_inspect_schema(ctx.deps, reason)
                self._emit_tool_completed(ctx.deps, "inspect_schema", arguments, result)
                return result

            tool_names.append("inspect_schema")

            @agent.tool
            async def skill_read(ctx: RunContext[MissionRuntime], ids: list[str], reason: str) -> ToolResult:
                arguments = {"ids": ids, "reason": reason}
                self._emit_tool_started(ctx.deps, "skill_read", arguments)
                result = await run_skill_read(ctx.deps, ids, reason)
                self._emit_tool_completed(ctx.deps, "skill_read", arguments, result)
                return result
            tool_names.append("skill_read")

            @agent.tool
            async def skill_write(ctx: RunContext[MissionRuntime], entries: list[dict[str, Any]], reason: str) -> ToolResult:
                arguments = {"entry_count": len(entries), "reason": reason}
                self._emit_tool_started(ctx.deps, "skill_write", arguments)
                result = await run_skill_write(ctx.deps, entries, reason)
                self._emit_tool_completed(ctx.deps, "skill_write", arguments, result)
                return result
            tool_names.append("skill_write")

            @agent.tool
            async def skill_update(ctx: RunContext[MissionRuntime], entries: list[dict[str, Any]], reason: str) -> ToolResult:
                arguments = {"entry_count": len(entries), "reason": reason}
                self._emit_tool_started(ctx.deps, "skill_update", arguments)
                result = await run_skill_update(ctx.deps, entries, reason)
                self._emit_tool_completed(ctx.deps, "skill_update", arguments, result)
                return result
            tool_names.append("skill_update")

            @agent.tool
            async def skill_deprecate(
                ctx: RunContext[MissionRuntime],
                ids: list[str],
                reason: str,
                replacement_id: str | None = None,
            ) -> ToolResult:
                arguments = {"ids": ids, "replacement_id": replacement_id, "reason": reason}
                self._emit_tool_started(ctx.deps, "skill_deprecate", arguments)
                result = await run_skill_deprecate(ctx.deps, ids, reason, replacement_id)
                self._emit_tool_completed(ctx.deps, "skill_deprecate", arguments, result)
                return result
            tool_names.append("skill_deprecate")

        if not runtime.services.settings.debug.disable_browser_tools and not is_skill_maintenance:
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
            target_model_type = self._build_target_model_type(runtime)

            async def switch_model(
                ctx: RunContext[MissionRuntime],
                target_model: str,
                reason: str,
            ) -> str:
                target_name = target_model.value if hasattr(target_model, "value") else str(target_model)
                arguments = {"target_model": target_name, "reason": reason}
                self._emit_tool_started(ctx.deps, "switch_model", arguments)
                try:
                    result = await request_model_switch(ctx.deps, target_name, reason)
                except ModelSwitchRequested as exc:
                    self._emit_tool_completed(
                        ctx.deps,
                        "switch_model",
                        arguments,
                        f"requested switch to {target_name}",
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
            switch_model.__annotations__["target_model"] = target_model_type
            agent.tool(switch_model)
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

    def _build_target_model_type(self, runtime: MissionRuntime) -> type[Enum]:
        allowed_models = runtime.context.allowed_models
        members = {
            self._sanitize_enum_name(model.name, index): model.name
            for index, model in enumerate(allowed_models)
        }
        return Enum("SwitchModelTarget", members, type=str)

    def _sanitize_enum_name(self, name: str, index: int) -> str:
        slug = [char.upper() if char.isalnum() else "_" for char in name]
        value = "".join(slug).strip("_")
        if not value:
            value = f"MODEL_{index}"
        if value[0].isdigit():
            value = f"MODEL_{value}"
        return value

    def _emit_tool_started(self, runtime: MissionRuntime, name: str, arguments: dict[str, Any]) -> None:
        emit_runtime_event(
            runtime.context,
            "tool.started",
            f"{name} started",
            {"name": name, "arguments": arguments},
            stream_event="tool.started",
            payload={"name": name, "parameters": arguments},
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
            result_summary = self._summarize_tool_result(name, result)
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
            payload={
                "name": name,
                "parameters": arguments,
                "ok": result_ok,
                "result_summary": result_summary,
                "error_type": error_type,
                "error_message": error_message,
            },
        )

    def _summarize_tool_result(self, name: str, result: ToolResult) -> str:
        if not result.ok:
            failure = result.error_message or result.retry_hint or name
            if result.error_type:
                return f"{result.error_type}: {failure}"
            return failure
        if name == "web_search" and isinstance(result.data, dict):
            hits = result.data.get("hits")
            if isinstance(hits, list):
                return f"returned {len(hits)} web result(s)"
        return self._summarize_value(result.data)

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
