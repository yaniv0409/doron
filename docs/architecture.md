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

1. FastAPI accepts either a low-level mission request or a named session request.
2. Session requests load durable session JSON, resolve the shared or dedicated DB path, and build a mission prompt from recent chat plus stored summary.
3. `MissionService` builds a mission-scoped runtime.
4. `AgentFactory` creates a `pydantic-ai` agent for the current model.
5. The agent uses explicit tools for graph work, browser work, documentation lookup, and model switching.
6. Structured output is validated against caller-provided JSON Schema.
7. Logs, session artifacts, and a normalized trace are written to disk.
8. When enabled, the main mission enqueues a durable skill-maintenance job and a `MaintenanceRunner` resumes it from persisted state.

## Boundaries

- `domain/`: pure types and exceptions
- `contracts/`: API payloads and serialization
- `application/`: mission flow, session orchestration, graph snapshots, and validation
- `agent/`: orchestration prompts and tool registration
- `infrastructure/`: Kuzu, Playwright, OpenRouter, logging, traces, docs lookup, maintenance job storage, session storage
- `tools/`: short capability functions used by the agent
