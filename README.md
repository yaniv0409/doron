# Agent Platform

Generic agent platform scaffold built around:

- `pydantic-ai` for orchestration
- Kuzu for graph persistence
- Playwright for web automation
- OpenRouter for model and embedding access

Web extraction behavior:

- Playwright navigation with `domcontentloaded` plus `networkidle` fallback
- dedicated 15-second browser navigation timeout by default, configurable through environment
- browser context configured to look like a normal desktop session
- cleaned readable page text without HTML tags
- structured extracted links from the rendered page

The codebase is organized for extension by both humans and coding agents:

- short functions
- explicit adapters and typed contracts
- rolling audit logs
- mission traces persisted to disk
- documentation in `docs/`

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
- install the selected Playwright browser

Manual path:

1. Create a virtualenv.
2. Install dependencies with `pip install -e ".[dev]"`.
3. Install Playwright browsers with `playwright install chromium`.
4. Set `OPENROUTER_API_KEY`.
5. Run the API:

```bash
uvicorn agent_platform.api.app:create_app --factory --reload
```

## API

`POST /missions/run`

Send:

- `prompt`
- `db_path`
- optional `output_schema`
- optional model controls
- optional `stream=true` to receive SSE events instead of blocking JSON

Receive:

- final result
- status
- final model
- trace id

Stream mode emits live mission progress, tool events, and the final result over SSE from the same endpoint.

See `docs/` for architecture and implementation details.

## Terminal chat

Run a terminal chat that talks to the API:

```bash
python -m agent_platform.cli.chat --db-path /absolute/path/to/database.kuzu --api-url http://127.0.0.1:8000
```

If you want the terminal to start a local API server automatically, add `--start-server`.
If the database path does not exist yet, the platform will initialize a new Kuzu database there on first use.
Prompts are multiline by default. Type lines freely and submit with a blank line.

Optional flags:

- `--preferred-model <model>`
- `--allowed-models model-a,model-b`
- `--output-schema /absolute/path/to/schema.json`
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
Live execution progress is written to `traces/<trace_id>/progress.json`, including browser-stage events such as navigation start, `domcontentloaded`, `networkidle`, fallback, timeout, and extraction.
