# Tooling Contracts

## Graph tools

- `graph_read(query, parameters)`
- `graph_write(query, parameters)`
- `graph_schema()`

Requirements:

- normalize result rows to Python dictionaries
- checkpoint the DB file before first mutation
- audit all mutating queries

## Browser tools

- `browser_open(url)`
- `browser_text()`

Requirements:

- mission-scoped browser lifecycle
- log visited URLs and extraction summaries

## Documentation tool

- `kuzu_reference(query)`

Requirements:

- read-only
- backed by local packaged documentation
- returns section-oriented excerpts with source identifiers

## Model control tool

- `switch_model(target_model, reason)`

Requirements:

- only switch to allowed models
- preserve mission context in a transfer packet
