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
7. compresses replaceable working memory when the model asks for cleanup or the configured context threshold is exceeded
8. enqueues post-mission memory maintenance when enabled

Recoverable tool failures do not automatically fail the mission. Database and documentation tool errors are returned to the agent as structured tool results so the agent can decide whether to inspect schema, retry differently, consult docs, switch tools, or answer without the DB.
Context compression never replaces the original mission prompt. The mission prompt stays canonical, is sent to the cleaner for relevance guidance, and the cleaner only replaces working memory.
Browser navigation uses a dedicated 15-second timeout by default. Browser timeouts are returned as recoverable tool errors, and browser-stage progress is written during navigation so a stuck page can be distinguished from a stalled model call.
Every tool call must include a short reason, and browser tools are capped at 20 calls per mission by default. That reason and the remaining web budget are surfaced in tool results and handoff prompts so the model keeps its direction and can stop browsing before it is rate limited.
Memory maintenance is durable and repo-owned. A main mission that finishes with maintenance enabled writes a maintenance job record under `traces/maintenance-jobs/`, writes an initial maintenance trace skeleton immediately, and lets the `MaintenanceRunner` execute the follow-up in the background. On startup the runner reloads pending jobs so maintenance does not depend on the request lifecycle that triggered the mission.
When the API is called with `stream=true`, the same mission emits live SSE events for mission progress, tool starts/completions, and the final result.

## Completion

The service returns:

- mission status
- final result
- result format
- final model
- trace id

Trace artifacts are written under `traces/<trace_id>/`.
Tool usage is recorded in the trace as ordered `tool_calls`. Specialized audit sections such as DB mutations, docs lookups, and web artifacts supplement the trace, but `trace.json` is the canonical answer to "what tools were used?".
Compression events are also recorded in the trace, including trigger type, summarizer model, size before/after, and a preview of the distilled memory.
`progress.json` provides the live operational view. It now includes browser-phase events, so the previous observability gap where only the outer agent timeout was visible is closed.
Maintenance runs also have a durable job record and an early trace skeleton so a stalled or cancelled maintenance pass can be diagnosed separately from the main mission.
The streamed API is the live UX path; it should be used by the terminal chat instead of reading trace files during the mission.
