"""SQLite persistence backends — Phase 4b.6.

Started with ``SqliteEventLogRepository`` as the first durable backend
for the Phase 4b.1 event log. Additional repositories (run, task,
workflow) may follow as the migration to an authoritative event-sourced
read model progresses.

Uses Python's stdlib ``sqlite3`` — no new runtime dependencies.
"""
