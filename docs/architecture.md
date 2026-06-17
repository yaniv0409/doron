# Architecture

## Goal

This repository is a generic agent platform scaffold.

Core runtime:

- `pydantic-ai` orchestrates mission execution
- Kuzu provides mutable graph persistence from a caller-supplied database path
- Playwright provides mission-scoped browser automation
- OpenRouter provides chat models and embeddings

## Design rules

- keep modules small
- isolate side effects behind adapters
- log all analytical activity to rotating files
- persist trace artifacts for every mission
- keep public contracts typed and stable
- this is an evolving project, not production software; prefer deleting dead code over preserving compatibility layers

## Runtime flow

1. FastAPI accepts a mission request and can return blocking JSON or streamed SSE.
2. `MissionService` builds a mission-scoped runtime.
3. `AgentFactory` creates a `pydantic-ai` agent for the current model.
4. The agent uses explicit tools for graph work, browser work, documentation lookup, and model switching.
5. Structured output is validated against caller-provided JSON Schema.
6. Logs and a normalized trace are written to disk.

## Boundaries

- `domain/`: pure types and exceptions
- `contracts/`: API payloads and serialization
- `application/`: mission flow and validation
- `agent/`: orchestration prompts and tool registration
- `infrastructure/`: Kuzu, Playwright, OpenRouter, logging, traces, docs lookup
- `tools/`: short capability functions used by the agent
