from __future__ import annotations

import argparse
from collections.abc import Sequence
from typing import Callable

from agent_platform.cli.formatters import format_final_stream_response, format_stream_event
from agent_platform.cli.session import ChatDefaults, ChatSession, load_output_schema


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    defaults = build_defaults(args)
    session = ChatSession(defaults)
    try:
        print("Agent Platform terminal chat")
        print("Commands: /help, /config, /exit")
        print("Enter a blank line to submit a multiline mission.")
        while True:
            prompt = read_prompt_block()
            if prompt is None:
                return 0
            if prompt in {"/exit", "/quit"}:
                return 0
            if prompt == "/help":
                print("Enter a mission prompt to run it. Use /config to inspect defaults. Use /exit to quit.")
                continue
            if prompt == "/config":
                print(format_config_summary(session.defaults))
                continue
            run_state = {
                "response": None,
                "tool_names": [],
                "trace_id": None,
            }
            for event in session.stream_prompt(prompt):
                if event.event == "tool.completed":
                    name = event.data.get("name")
                    if isinstance(name, str):
                        run_state["tool_names"].append(name)
                if event.event in {"mission.completed", "mission.failed"}:
                    run_state["response"] = event.data
                    run_state["trace_id"] = event.data.get("trace_id")
                print(format_stream_event(event))
            response_data = run_state["response"]
            if response_data is None:
                print("Mission ended without a final response")
                continue
            from agent_platform.contracts.api import MissionRunResponse

            response = MissionRunResponse.model_validate(response_data)
            print(format_final_stream_response(response, run_state["tool_names"]))
    finally:
        session.close()
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Terminal chat for agent-platform.")
    parser.add_argument("--db-path")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--start-server", action="store_true", default=False)
    parser.add_argument("--server-ready-timeout-seconds", type=int, default=20)
    parser.add_argument("--preferred-model")
    parser.add_argument("--allowed-models")
    parser.add_argument("--output-schema")
    parser.add_argument("--web-enabled", dest="web_enabled", action="store_true", default=True)
    parser.add_argument("--no-web", dest="web_enabled", action="store_false")
    parser.add_argument("--db-mutation-enabled", dest="db_mutation_enabled", action="store_true", default=True)
    parser.add_argument("--no-db-mutation", dest="db_mutation_enabled", action="store_false")
    return parser.parse_args(argv)


def build_defaults(args: argparse.Namespace) -> ChatDefaults:
    db_path = args.db_path or input("Database path: ").strip()
    print(f"Using database path: {db_path}")
    allowed_models = parse_allowed_models(args.allowed_models)
    return ChatDefaults(
        db_path=db_path,
        api_url=args.api_url,
        preferred_model=args.preferred_model,
        allowed_models=allowed_models,
        web_enabled=args.web_enabled,
        db_mutation_enabled=args.db_mutation_enabled,
        output_schema=load_output_schema(args.output_schema),
        start_server=args.start_server,
        server_ready_timeout_seconds=args.server_ready_timeout_seconds,
    )


def parse_allowed_models(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def read_prompt_block(input_fn: Callable[[str], str] = input) -> str | None:
    lines: list[str] = []
    while True:
        prompt = "You> " if not lines else "...> "
        try:
            line = input_fn(prompt)
        except EOFError:
            if lines:
                return "\n".join(lines)
            return None
        if not lines and line.strip() in {"/help", "/config", "/exit", "/quit"}:
            return line.strip()
        if line == "":
            if lines:
                return "\n".join(lines)
            continue
        lines.append(line)


if __name__ == "__main__":
    raise SystemExit(main())
