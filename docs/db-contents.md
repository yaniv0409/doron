# DB Contents API

`POST /db/contents`

Read-only endpoint for end apps that need to inspect a Kuzu database file directly.

Request body:

- `db_path`
- `sample_limit`
- `include_schema`
- `include_counts`
- `include_connections`

Behavior:

- opens the database in read-only mode
- discovers whatever tables exist at request time
- reuses Kuzu introspection queries for table inventory, schema, and relation metadata
- returns bounded sample rows per discovered table
- never mutates the database

Response fields:

- `db_path`
- `generated_at`
- `table_count`
- `tables`

Each table includes:

- table name and kind
- schema rows when enabled
- row counts when enabled
- sample rows
- relation connection metadata when applicable

This endpoint is separate from mission execution and is intended for end applications that want to read whatever schema currently exists in the database directly.
