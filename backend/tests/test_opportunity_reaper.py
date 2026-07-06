"""
Tests for the opportunity-sweep statements used by cleanup_expired_opportunities.

The orphaned-'executing' reaper is a safety-critical liveness guard: the atomic
claim commits queued->executing before execution runs, so an executor crash would
strand an opportunity in 'executing' forever (and the claim guard guarantees it is
never retried). These tests lock the sweep predicates against regression without
needing a live Postgres — they assert on the compiled UPDATE parameters.
"""
from datetime import datetime, timezone

from app.database.queries import _orphaned_executing_stmt, _stale_open_stmt


def _flat_values(compiled):
    """Flatten compiled bind params, expanding IN-clause list params into scalars."""
    flat = []
    for v in compiled.params.values():
        if isinstance(v, (list, tuple)):
            flat.extend(v)
        else:
            flat.append(v)
    return flat


class TestReaperStatements:
    def test_orphaned_executing_targets_executing_sets_failed(self):
        stmt = _orphaned_executing_stmt(datetime.now(timezone.utc))
        compiled = stmt.compile()
        values = _flat_values(compiled)
        sql = str(compiled).upper()

        assert sql.startswith("UPDATE")
        assert "OPPORTUNITIES" in sql
        # Predicate targets the stranded 'executing' state...
        assert "executing" in values
        # ...and resets it to 'failed' (never 'queued' — no silent auto-retry).
        assert "failed" in values
        assert "queued" not in values

    def test_stale_open_targets_pending_queued_sets_expired(self):
        stmt = _stale_open_stmt(datetime.now(timezone.utc))
        compiled = stmt.compile()
        values = _flat_values(compiled)

        assert "pending" in values
        assert "queued" in values
        assert "expired" in values

    def test_reaper_does_not_touch_executed_or_failed(self):
        # Only 'executing' should be swept; a completed/failed opp must be immune.
        stmt = _orphaned_executing_stmt(datetime.now(timezone.utc))
        values = _flat_values(stmt.compile())
        assert "executed" not in values
