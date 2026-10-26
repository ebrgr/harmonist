"""Upgrade existing databases without replacing history or release evidence."""

import sqlite3

from harmonist import activity_store
from test.test_artist_web import ALICE


def test_upgrade_v11_and_reopen_preserve_existing_history_and_artist_review(tmp_path):
    db = tmp_path / "activity.db"
    with sqlite3.connect(db) as conn:
        for migration in activity_store._MIGRATIONS[:11]:
            for statement in migration:
                conn.execute(statement)
        conn.execute("PRAGMA user_version = 11")
        conn.execute(
            "INSERT INTO events (ts, level, source, message, artist_id) VALUES ('2026-10-25T12:00:00+00:00', 'info', 'audit', 'artist_nfo.saved', ?)",
            (ALICE,),
        )
        conn.execute(
            "INSERT INTO mb_release_cache VALUES ('release', 'original-includes', '2026-10-25T12:00:00+00:00', '{}')"
        )
    activity_store.init(db)
    activity_store.store_artist_document("artist", {"name": "Original"})
    activity_store.store_artist_document("review", {"name": "Reviewed"}, review=True)
    activity_store.store_artist_document("review", {"name": "Wrong replacement"}, review=True)
    activity_store.store_artist_document("artist", {"name": "New cache"})
    activity_store.init(db)
    assert activity_store.artist_document("review", review=True).payload == {"name": "Reviewed"}
    assert activity_store.artist_document("artist").payload == {"name": "New cache"}
    assert [e.message for e in activity_store.artist_history(ALICE)] == ["artist_nfo.saved"]
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == activity_store.SCHEMA_VERSION
        assert conn.execute("SELECT inc FROM mb_release_cache").fetchone()[0] == "original-includes"
    activity_store.clear()
    assert activity_store.artist_document("review", review=True) is None
    assert activity_store.artist_document("artist") is None
