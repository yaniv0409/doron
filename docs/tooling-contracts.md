# Tooling Contracts

## Graph tools

- `graph_read(query, parameters, reason)`
- `graph_write(query, parameters, reason)`
- `graph_schema(reason)`

Requirements:

- normalize result rows to Python dictionaries
- checkpoint the DB file before first mutation
- audit all mutating queries
- return structured tool results with `ok`, `error_type`, `error_message`, `retry_hint`, and `data`
- require a short reason on every tool call
- return recoverable DB query failures to the agent instead of aborting the mission immediately

## Browser tools

- `browser_open(urls, reason)`
- `browser_text(reason)`

Requirements:

- `browser_text` uses the mission-scoped browser page and can lazy-start it on first use
- `browser_open` fetches each URL in its own short-lived Playwright session and closes that session after extraction
- look like a normal desktop browser session using built-in Playwright context settings
- enforce a dedicated browser navigation timeout with a 15-second default
- wait for `domcontentloaded` and then `networkidle`
- extract readable main-content text with no HTML tags in the final text payload
- return structured links with `text`, `href`, and optional `title`
- fetch multiple URLs in parallel through a configurable thread pool
- preserve input order and return partial success when some URLs fail
- cap browser tool usage at 20 calls per mission by default
- require a short reason on every tool call
- log visited URLs and extraction summaries
- write browser-stage progress events for the mission-scoped browser so stalls can be attributed to navigation, `networkidle`, or extraction
- emit batch-level browser events for `browser_open` start, completion, and per-URL success/failure
- prefer structured tool results so recoverable browser issues can be reasoned about by the agent

## Mission stream

- `POST /missions/run` accepts `stream=true` for SSE mode
- stream events include mission progress, tool starts/completions, and final result/failure
- the terminal chat consumes the streamed API, not the internal runtime directly

## Documentation tool

- `kuzu_reference(query, reason)`

Requirements:

- read-only
- backed by local packaged documentation
- returns section-oriented excerpts with source identifiers
- returns recoverable lookup errors as structured tool results

## Model control tool

- `switch_model(target_model, reason)`

Requirements:

- only switch to allowed models
- preserve mission context in a transfer packet
