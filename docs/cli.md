# CLI

## Launch

Run the terminal chat against the API:

```bash
python -m agent_platform.cli.chat --db-path /absolute/path/to/database.kuzu --api-url http://127.0.0.1:8000
```

If `--db-path` is omitted, the CLI prompts for it at startup.
If the path does not exist, the runtime creates a new Kuzu database at that location.
The terminal talks to the API over HTTP and uses the streamed mission mode by default.
Prompts are multiline by default: type lines freely and submit the mission with a blank line.

## Flags

- `--preferred-model <model>`
- `--allowed-models model-a,model-b`
- `--output-schema /absolute/path/to/schema.json`
- `--api-url http://127.0.0.1:8000`
- `--start-server`
- `--server-ready-timeout-seconds <seconds>`
- `--no-web`
- `--no-db-mutation`

## Commands

- `/help`
- `/config`
- `/exit`
- `/quit`

## Output

Each prompt is a fresh mission. After completion, the CLI prints:

- status
- result
- final model
- trace id
- compact ordered tool summary

While the mission is running, it prints live stream updates for:

- mission progress
- tool starts and completions
- the final result event

If you enter `/help`, `/config`, `/exit`, or `/quit` as the first line of a prompt, the CLI treats it as a command. If you already started a multiline prompt, slash-prefixed lines are treated as prompt text.

Example summary:

```text
Tools: inspect_schema -> read_graph -> lookup_kuzu_docs
```

## Traces vs logs

- `traces/<trace_id>/trace.json` is the canonical mission record
- `logs/` contains rolling operational logs
- `traces/<trace_id>/progress.json` shows live progress, including browser phase events
- Use the streamed API events for live UI output, not the trace file.

Use the trace file when you need a reliable answer to which tools were used and in what order.
