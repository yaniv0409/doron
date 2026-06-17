# Model Routing

## Registry

Models are configured as ranked descriptors:

- model name
- intelligence rank
- context window
- cost class
- tool support
- structured-output suitability

## Policy

- start from the preferred model if allowed
- otherwise use the configured default
- allow the agent to request a stronger model
- escalate automatically after output-validation failures when a stronger allowed model exists

## Context transfer

The handoff packet includes:

- original mission prompt
- reasoning notes
- database findings
- web findings
- tool summaries

When context compression runs, the original mission prompt remains unchanged and only the replaceable working-memory portion is distilled.
