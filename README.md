# Agent Platform

Generic agent platform scaffold built around:

- `pydantic-ai` for orchestration
- Kuzu for graph persistence
- Playwright for web automation
- OpenRouter for model and embedding access

The codebase is organized for extension by both humans and coding agents:

- short functions
- explicit adapters and typed contracts
- rolling audit logs
- mission traces persisted to disk
- documentation in `docs/`

## Quick start

1. Install dependencies.
2. Install Playwright browsers: `playwright install chromium`
3. Set `OPENROUTER_API_KEY`.
4. Run the API:

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

Receive:

- final result
- status
- final model
- trace id

See `docs/` for architecture and implementation details.
