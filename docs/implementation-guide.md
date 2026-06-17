# Implementation Guide

## Build order

1. Define domain and API contracts.
2. Implement infrastructure adapters with narrow interfaces.
3. Build mission-scoped runtime assembly.
4. Register short tool functions with the agent.
5. Add API routes with blocking and streamed mission modes.
6. Add the terminal client as an API consumer.
7. Add integration tests around real Kuzu and Playwright environments.

## Coding rules

- keep functions short
- prefer typed inputs and outputs
- avoid cross-layer imports upward
- log with `trace_id`
- persist request snapshots and final traces
- treat the API as the stable user-facing control surface
- this is an evolving repo, not a production compatibility layer; delete dead code instead of preserving shims

## High-value extension points

- replace Playwright engine with another `BrowserEngine`
- add richer Kuzu schema inspection and object management
- add durable mission storage
- add streaming API or queued async jobs
- add retrieval features on top of the embedding client
