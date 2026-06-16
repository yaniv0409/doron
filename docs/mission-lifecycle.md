# Mission Lifecycle

## Request

The API receives:

- prompt
- database path
- optional output schema
- optional model preferences
- feature flags for web and database mutation

If the database path does not exist, the runtime initializes a new Kuzu database there before the mission runs.

## Execution

`MissionService`:

1. builds `MissionRuntime`
2. snapshots the request
3. runs the current model through `pydantic-ai`
4. records tool use, documentation lookups, and graph mutations
5. performs model handoff if requested
6. validates the final result

Recoverable tool failures do not automatically fail the mission. Database and documentation tool errors are returned to the agent as structured tool results so the agent can decide whether to inspect schema, retry differently, consult docs, switch tools, or answer without the DB.

## Completion

The service returns:

- mission status
- final result
- result format
- final model
- trace id

Trace artifacts are written under `traces/<trace_id>/`.
Tool usage is recorded in the trace as ordered `tool_calls`. Specialized audit sections such as DB mutations, docs lookups, and web artifacts supplement the trace, but `trace.json` is the canonical answer to "what tools were used?".
