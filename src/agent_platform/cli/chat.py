from __future__ import annotations

import argparse
from collections.abc import Sequence

from agent_platform.cli.formatters import format_config_summary, format_response
from agent_platform.cli.session import ChatDefaults, ChatSession, load_output_schema


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    defaults = build_defaults(args)
    session = ChatSession(defaults)
    print("Agent Platform terminal chat")
    print("Commands: /help, /config, /exit")
    while True:
        prompt = input("You> ").strip()
        if not prompt:
            continue
        if prompt in {"/exit", "/quit"}:
            return 0
        if prompt == "/help":
            print("Enter a mission prompt to run it. Use /config to inspect defaults. Use /exit to quit.")
            continue
        if prompt == "/config":
            print(format_config_summary(session.defaults))
            continue
        response, trace = session.run_prompt(prompt)
        print(format_response(response, trace))
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Terminal chat for agent-platform.")
    parser.add_argument("--db-path")
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
        preferred_model=args.preferred_model,
        allowed_models=allowed_models,
        web_enabled=args.web_enabled,
        db_mutation_enabled=args.db_mutation_enabled,
        output_schema=load_output_schema(args.output_schema),
    )


def parse_allowed_models(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


if __name__ == "__main__":
    raise SystemExit(main())
