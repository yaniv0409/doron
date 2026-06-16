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

Receive:

- final result
- status
- final model
- trace id

See `docs/` for architecture and implementation details.
