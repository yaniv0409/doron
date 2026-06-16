# CLI

## Launch

Run the terminal chat directly in-process:

```bash
python -m agent_platform.cli.chat --db-path /absolute/path/to/database.kuzu
```

If `--db-path` is omitted, the CLI prompts for it at startup.

## Flags

- `--preferred-model <model>`
- `--allowed-models model-a,model-b`
- `--output-schema /absolute/path/to/schema.json`
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

Example summary:

```text
Tools: inspect_schema -> read_graph -> lookup_kuzu_docs
```

## Traces vs logs

- `traces/<trace_id>/trace.json` is the canonical mission record
- `logs/` contains rolling operational logs

Use the trace file when you need a reliable answer to which tools were used and in what order.
