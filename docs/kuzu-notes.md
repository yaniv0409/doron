# Kuzu Notes

This file is both human documentation and the source for the agent's Kuzu reference tool.

## Connection Model

Kuzu is opened from a local database path. The platform uses one mission-scoped connection and exposes read, write, and schema-inspection tools through that connection.

## Query Shape

Use Cypher-style queries. Prefer parameterized queries when values are dynamic. Keep mutating queries explicit and small so audit logs remain understandable.

## Schema Introspection

The platform exposes a schema-inspection tool. Use it before creating or modifying graph structures when the existing schema is unknown.

## Mutations

The platform allows writes and schema changes when database mutation is enabled for the mission. Every mutation is audited, and the database file is checkpointed before the first mutation attempt.

## Node and Relationship Tables

The agent may create node tables and relationship tables if the mission requires new graph structure. Check existing schema first to avoid duplicate structures.

## Troubleshooting

If a query fails, inspect the schema, reduce the query to a smaller form, and consult this reference tool again for the relevant topic before retrying.
