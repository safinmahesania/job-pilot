"""Deleting a source also clears its health, so a removed board stops showing stale
errors; and prune-health clears any already-orphaned rows."""

from src import health


def _seed_health(conn, name, error_streak=30):
    conn.execute(
        "INSERT INTO source_health (name, ats, fetched, kept, status, error, "
        "last_run, zero_streak, error_streak, last_ok, alerted) "
        "VALUES (?, 'greenhouse', 0, 0, 'error', '404', '2026-01-01', 0, ?, NULL, false)",
        (name, error_streak))
    conn.commit()


class TestPruneHealth:
    def test_assess_lists_seeded_board(self, conn):
        _seed_health(conn, "Acme")
        names = [b["name"] for b in health.assess(conn)]
        assert "Acme" in names

    def test_prune_removes_orphan_not_in_config(self, conn, monkeypatch):
        _seed_health(conn, "Acme")
        # No companies configured → Acme is an orphan.
        monkeypatch.setattr("src.configio.read_yaml", lambda *_: {"companies": []})
        from src.routes import sources
        result = sources.prune_orphaned_health(conn=conn)
        assert "Acme" in result["pruned"]
        assert "Acme" not in [b["name"] for b in health.assess(conn)]

    def test_prune_keeps_live_board(self, conn, monkeypatch):
        _seed_health(conn, "Shopify")
        monkeypatch.setattr("src.configio.read_yaml",
                            lambda *_: {"companies": [{"name": "Shopify"}]})
        from src.routes import sources
        result = sources.prune_orphaned_health(conn=conn)
        assert "Shopify" not in result["pruned"]
