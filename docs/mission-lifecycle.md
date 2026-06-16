# Mission Lifecycle

## Request

The API receives:

- prompt
- database path
- optional output schema
- optional model preferences
- feature flags for web and database mutation

## Execution

`MissionService`:

1. builds `MissionRuntime`
2. snapshots the request
3. runs the current model through `pydantic-ai`
4. records tool use, documentation lookups, and graph mutations
5. performs model handoff if requested
6. validates the final result

## Completion

The service returns:

- mission status
- final result
- result format
- final model
- trace id

Trace artifacts are written under `traces/<trace_id>/`.
