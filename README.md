# Doron

Doron is a generic agent scaffold built around:

- `pydantic-ai` for orchestration
- Kuzu for graph persistence
- Playwright for web automation
- a visible `web_search` tool backed by DuckDuckGo
- OpenRouter for model and embedding access

Research workspace features:

- named durable research sessions stored as JSON under `sessions/`
- shared or dedicated research storage directories under `dbs/`
- each storage directory contains `memory.kuzu` and `research_meta.kuzu`
- shared DB session groups so multiple sessions can work against the same storage directory with separate context
- live per-request SSE streaming for mission progress and tool activity
- session-scoped web browsing tool limits with per-message overrides
- session stop controls for soft wrap-up requests and hard session cancellation
- live session steering that restarts the current mission with appended guidance
- a React web UI under `web/` with chat, sessions, and graph inspection

Web extraction behavior:

- Playwright navigation with `domcontentloaded` and `networkidle`
- dedicated 15-second browser navigation timeout by default, configurable through environment
- browser context configured to look like a normal desktop session
- cleaned readable page text without HTML tags
- structured extracted links from the rendered page
- visible `web_search(query, reason)` discovery tool with compact search hits
- `browser_text(reason)` uses the mission-scoped browser page and lazily starts it on first use
- batch `browser_open(urls)` fetches multiple URLs in parallel with a configurable worker pool
- each `browser_open` URL is fetched in its own short-lived Playwright session and closed after extraction
- batch results preserve input order and may contain partial failures
- browser tools are capped at 20 calls per mission by default, and every tool call carries a reason

The codebase is organized for extension by both humans and coding agents:

- short functions
- explicit adapters and typed contracts
- rolling audit logs
- mission traces persisted to disk
- documentation in `docs/`
- this is an evolving project, not a production compatibility target; prefer deleting dead code over preserving shims

## Quick start

Bootstrap and configure everything with:

```bash
./scripts/setup.sh
```

The script is interactive and reusable. It will:

- ask for your OpenRouter API key
- ask for the embedding model and ranked chat models
- ask for browser, logging, trace, and docs-path settings
- write `.env`
- write `config/models.json`
- create or reuse `.venv`
- install Python dependencies
- install web dependencies under `web/`
- install the selected Playwright browser

Manual path:

1. Create a virtualenv.
2. Install dependencies with `pip install -e ".[dev]"`.
3. Install web dependencies with `cd web && npm install`.
4. Install Playwright browsers with `playwright install chromium`.
5. Set `OPENROUTER_API_KEY`.
6. Run the API:

```bash
uvicorn agent_platform.api.app:create_app --factory --reload
```

## API

`POST /missions/run`

Send:

- `prompt`
- `memory_db_path`
- `research_meta_db_path`
- optional `output_schema`
- optional model controls
- optional `stream=true` to receive SSE events instead of blocking JSON

Receive:

- final result
- status
- final model
- trace id

Stream mode emits live mission progress, tool events, and the final result over SSE from the same endpoint.

## Session API

`POST /sessions/open`

- create or resume a named session
- returns a stable `session_id`
- uses `dbs/shared/` by default
- creates a dedicated `dbs/<session-name>-<id>/` directory when requested
- accepts optional `session_group_id` to create or resume a session inside an existing shared-DB group

`GET /sessions`

- list sessions for the sidebar

`GET /sessions/{session_id}`

- full session metadata and chat history

`PATCH /sessions/{session_id}`

- update session defaults such as `web_tool_call_limit`

`POST /sessions/{session_id}/stop`

- persist a soft wrap-up request or hard-stop the session

`POST /sessions/{session_id}/resume`

- clear the stop state and reopen a stopped session

`POST /sessions/{session_id}/fork`

- create a new session on the same DB as the source session
- supports explicit inheritance flags for model settings, output schema, runtime settings, and context
- creates a shared session group when the source session is not already grouped

`POST /sessions/{session_id}/steer`

- append a live steer message to the active session
- cancels the in-flight mission and restarts it with the original mission plus the new steer context
- requires an active run

`POST /sessions/{session_id}/chat`

- blocking chat response

`POST /sessions/{session_id}/chat/stream`

- streamed SSE chat response
- emits session events plus the existing mission/tool/progress events
- steering during a streamed run stays on the same SSE stream and surfaces `session.steered` and `session.restarted`

`GET /sessions/{session_id}/graph`

- graph snapshot for the session database target
- nodes and edges include their metadata for click inspection in the UI

`POST /db/contents`

Read-only database snapshot endpoint for end apps. It discovers whatever tables exist in a Kuzu database file given in `db_path` and returns a generic `tables` snapshot with optional schema, counts, connections, and bounded sample rows.

See `docs/` for architecture and implementation details.

## Web UI

The web UI lives in `web/` and talks to the session API.

Run it with:

```bash
cd web
npm install
npm run dev
```

By default the frontend calls `http://127.0.0.1:8000`. Override with:

```bash
VITE_API_BASE_URL=http://127.0.0.1:8000 npm run dev
```

While a mission is running, submitting another message from the web chat becomes a steer instead of opening a new mission. Doron appends that steer to the active session timeline, restarts the mission, and continues from the accumulated session context.
The web sidebar also groups sessions that share the same DB, and right-clicking a session opens a fork action that creates a sibling session on that DB.

To start both the backend and the frontend together from the repo root:

```bash
./scripts/dev.sh
```

Optional overrides:

- `BACKEND_HOST=127.0.0.1`
- `BACKEND_PORT=8000`
- `FRONTEND_HOST=127.0.0.1`
- `FRONTEND_PORT=5173`
- `PYTHON_BIN=/path/to/python`
- `VITE_API_BASE_URL=http://127.0.0.1:8000`

## Terminal chat

Run a terminal chat that talks to the API:

```bash
python -m agent_platform.cli.chat --db-dir /absolute/path/to/database-directory --api-url http://127.0.0.1:8000
```

If you want the terminal to start a local API server automatically, add `--start-server`.
If the database directory does not exist yet, the platform will initialize both Kuzu databases there on first use.
Prompts are multiline by default. Type lines freely and submit with a blank line.
If you pass `--prompt-file`, the CLI loads that file as the first mission prompt before it drops into interactive mode.

Optional flags:

- `--preferred-model <model>`
- `--allowed-models model-a,model-b`
- `--output-schema /absolute/path/to/schema.json`
- `--prompt-file /absolute/path/to/prompt.md`
- `--api-url http://127.0.0.1:8000`
- `--start-server`
- `--no-web`
- `--no-db-mutation`

After each prompt, the terminal prints:

- the mission result
- the final model
- the trace id
- a compact ordered list of tools used

If `/help`, `/config`, `/exit`, or `/quit` is entered as the first line, it is treated as a command. Once a multiline prompt is in progress, slash-prefixed lines are treated as normal prompt text.

Tool usage is streamed live from the API as tool events, traced canonically in `traces/<trace_id>/trace.json`, and also written to rolling logs under `logs/`.
Live execution progress is written to `traces/<trace_id>/progress.json`, including browser-stage events such as navigation start, `domcontentloaded`, `networkidle`, timeout, and extraction.
