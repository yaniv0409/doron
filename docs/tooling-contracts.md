# Tooling Contracts

## Graph tools

- `graph_read(query, parameters)`
- `graph_write(query, parameters)`
- `graph_schema()`

Requirements:

- normalize result rows to Python dictionaries
- checkpoint the DB file before first mutation
- audit all mutating queries
- return structured tool results with `ok`, `error_type`, `error_message`, `retry_hint`, and `data`
- return recoverable DB query failures to the agent instead of aborting the mission immediately

## Browser tools

- `browser_open(url)`
- `browser_text()`

Requirements:

- mission-scoped browser lifecycle
- look like a normal desktop browser session using built-in Playwright context settings
- enforce a dedicated browser navigation timeout with a 15-second default
- wait for `domcontentloaded` and then attempt `networkidle` with a bounded fallback
- extract readable main-content text with no HTML tags in the final text payload
- return structured links with `text`, `href`, and optional `title`
- log visited URLs and extraction summaries
- write browser-stage progress events so stalls can be attributed to navigation, `networkidle`, or extraction
- prefer structured tool results so recoverable browser issues can be reasoned about by the agent

## Documentation tool

- `kuzu_reference(query)`

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
