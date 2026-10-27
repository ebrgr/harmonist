"""NFO history records durable changes and restores only unchanged targets."""
# ruff: noqa: F811 -- imported pytest fixtures

import hashlib
import sqlite3
from zoneinfo import ZoneInfo

import pytest
from bs4 import BeautifulSoup, Tag

from harmonist import activity_store, artist_nfo, artist_nfo_edit
from test.test_artist_nfo_edit import local_nfo  # noqa: F401
from test.test_artist_web import ALICE, BOB, RELEASE, artist_client  # noqa: F401
from test.test_web import zone  # noqa: F401


@pytest.mark.parametrize("path", [f"/artist/{ALICE}", f"/album/{RELEASE}"])
def test_shared_history_shows_local_time_and_preserves_the_utc_instant(artist_client, zone, path):
    client, _cfg, network = artist_client
    zone("America/New_York")
    activity_store.append(
        message="Metadata reviewed",
        level=activity_store.Level.INFO,
        source=activity_store.Source.ACTIVITY,
        album_id=RELEASE,
        artist_id=ALICE,
    )
    event = next(
        e for e in activity_store.artist_history(ALICE) if e.message == "Metadata reviewed"
    )
    local = event.ts.astimezone(ZoneInfo("America/New_York"))
    body = BeautifulSoup(client.get(path).text, "html.parser")
    row = next(
        row for row in body.select(".album-history li") if "Metadata reviewed" in row.get_text()
    )
    stamp = row.find("time")
    assert isinstance(stamp, Tag)
    assert stamp.get_text(strip=True) == f"{local:%Y-%m-%d %H:%M}"
    assert stamp["datetime"] == event.ts.isoformat()
    assert stamp["title"] == f"{local:%Y-%m-%d %H:%M:%S %Z} ({event.ts:%H:%M:%S} UTC)"
    network.assert_not_called()


def test_upgrade_adds_artist_history_without_changing_existing_records(tmp_path):
    db = tmp_path / "old.db"
    with sqlite3.connect(db) as conn:
        for step in activity_store._MIGRATIONS[:10]:
            for statement in step:
                conn.execute(statement)
        conn.execute("PRAGMA user_version=10")
        conn.execute(
            "INSERT INTO events(ts,level,source,message,album_id) VALUES(?,?,?,?,?)",
            ("2026-10-25T00:00:00+00:00", "info", "activity", "Old album event", "album"),
        )
    activity_store.init(db)
    assert activity_store.recent()[0].artist_id is None
    assert activity_store.album_history("album")[0].message == "Old album event"
    assert activity_store.artist_history(ALICE) == []
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == activity_store.SCHEMA_VERSION
        assert conn.execute("SELECT count(*) FROM artist_nfo_changes").fetchone()[0] == 0
    activity_store.init(db)
    assert len(activity_store.recent()) == 1


def test_save_records_hashes_fields_and_artist_then_undo_preserves_both_versions(local_nfo):
    artist, music, info, path, _old_backups, db = local_nfo
    backups = db.parent / "nfoartists"
    original = path.read_bytes()
    fingerprint = hashlib.sha256(original).hexdigest()
    artist_nfo_edit.save(artist, music, info, backups, {"genre": "Rock"}, fingerprint)
    edited = path.read_bytes()
    history = activity_store.artist_history(ALICE)
    details = activity_store.nfo_changes_for([event.id for event in history])
    assert len(details) == 1
    event_id, change = next(iter(details.items()))
    assert change.file == str(path.resolve())
    assert change.before_hash == fingerprint
    assert change.after_hash == hashlib.sha256(edited).hexdigest()
    assert change.changes == {"genre": ["Jazz", "Rock"]}
    assert activity_store.artist_history(BOB) == []
    assert len({event.action_id for event in history}) == 1
    assert history[0].action_id is not None
    assert (backups / f"{fingerprint}.nfo").read_bytes() == original
    activity_store.init(db)
    restored = artist_nfo_edit.restore(artist, music, info, backups, event_id)
    assert restored.changed
    assert path.read_bytes() == original
    after_hash = hashlib.sha256(edited).hexdigest()
    assert (backups / f"{after_hash}.nfo").read_bytes() == edited
    history = activity_store.artist_history(ALICE)
    details = activity_store.nfo_changes_for([event.id for event in history])
    undo_id = next(key for key, value in details.items() if value.before_hash == after_hash)
    artist_nfo_edit.restore(artist, music, info, backups, undo_id)
    assert path.read_bytes() == edited
    count = len(activity_store.artist_history(ALICE))
    assert not artist_nfo_edit.save(
        artist, music, info, backups, {"genre": "Rock"}, fingerprint
    ).changed
    assert len(activity_store.artist_history(ALICE)) == count


@pytest.mark.parametrize(
    "obstacle", ["external", "missing_backup", "corrupt_backup", "other_artist"]
)
def test_undo_refuses_changed_files_missing_backups_and_foreign_events(local_nfo, obstacle):
    artist, music, info, path, backups, _db = local_nfo
    original = path.read_bytes()
    fingerprint = hashlib.sha256(original).hexdigest()
    artist_nfo_edit.save(artist, music, info, backups, {"genre": "Rock"}, fingerprint)
    history = activity_store.artist_history(ALICE)
    event_id = next(iter(activity_store.nfo_changes_for([e.id for e in history])))
    if obstacle == "external":
        path.write_bytes(path.read_bytes().replace(b"Rock", b"External"))
    elif obstacle == "missing_backup":
        (backups / f"{fingerprint}.nfo").unlink()
    elif obstacle == "corrupt_backup":
        (backups / f"{fingerprint}.nfo").write_bytes(original.replace(b"Jazz", b"Blues"))
    else:
        from dataclasses import replace

        artist = replace(artist, key=BOB, mbid=BOB)
    before = path.read_bytes()
    with pytest.raises(artist_nfo.NfoError):
        artist_nfo_edit.restore(artist, music, info, backups, event_id)
    assert path.read_bytes() == before


def test_artist_history_shows_fields_and_restores_using_only_an_event_id(artist_client):
    client, cfg, network = artist_client
    page = client.get(f"/artist/{ALICE}")
    profile = page.context["nfo"].profile
    original = profile.path.read_bytes()
    audio = {path: path.read_bytes() for path in cfg.paths.music_dir.rglob("*.m4a")}
    result = client.post(
        f"/artist/{ALICE}/nfo",
        data={"expected": profile.fingerprint, "genre": "History genre", "born": "1981"},
    )
    assert "HX-Redirect" in result.headers
    assert (
        cfg.paths.config_dir / "nfoartists" / f"{profile.fingerprint}.nfo"
    ).read_bytes() == original
    page = client.get(f"/artist/{ALICE}")
    assert "History genre" in page.text
    assert "Show details" in page.text
    assert "1980" in page.text and "1981" in page.text
    changes = page.context["nfo_changes"]
    assert len(changes) == 1
    event_id = next(iter(changes.values())).event_id
    foreign = client.post(f"/artist/{BOB}/nfo/restore", data={"event_id": event_id})
    assert foreign.status_code == 404
    restored = client.post(f"/artist/{ALICE}/nfo/restore", data={"event_id": event_id})
    assert restored.status_code == 200
    assert "HX-Redirect" in restored.headers
    assert profile.path.read_bytes() == original
    assert {path: path.read_bytes() for path in audio} == audio
    assert "Artist NFO restored" in client.get(f"/artist/{ALICE}").text
    network.assert_not_called()


def test_history_read_error_does_not_claim_no_edits(artist_client, monkeypatch):
    client, _cfg, _network = artist_client

    def unavailable(*args, **kwargs):
        raise activity_store.StoreUnavailableError("unavailable")

    monkeypatch.setattr(activity_store, "artist_history", unavailable)
    page = client.get(f"/artist/{ALICE}")
    assert page.status_code == 200
    assert "History unavailable" in page.text
    assert "Nothing recorded for this artist" not in page.text
    assert len(page.context["rows"]) == 2


def test_undo_creation_removes_only_that_nfo_and_can_itself_be_undone(local_nfo):
    from dataclasses import replace

    artist, music, info, _path, backups, _db = local_nfo
    artist = replace(artist, key=BOB, mbid=BOB)
    saved = artist_nfo_edit.save(artist, music, info, backups, {"genre": "Rock"}, "")
    original = saved.path.read_bytes()
    events = activity_store.artist_history(BOB)
    event_id = next(iter(activity_store.nfo_changes_for([e.id for e in events])))
    artist_nfo_edit.restore(artist, music, info, backups, event_id)
    assert not saved.path.exists()
    count = len(activity_store.artist_history(BOB))
    assert not artist_nfo_edit.restore(artist, music, info, backups, event_id).changed
    assert len(activity_store.artist_history(BOB)) == count
    events = activity_store.artist_history(BOB)
    details = activity_store.nfo_changes_for([e.id for e in events])
    removed = next(c for c in details.values() if c.after_hash is None)
    artist_nfo_edit.restore(artist, music, info, backups, removed.event_id)
    assert saved.path.read_bytes() == original


def test_undo_refuses_when_it_cannot_back_up_the_current_version(local_nfo, monkeypatch):
    artist, music, info, path, backups, _db = local_nfo
    profile = artist_nfo.read(path)
    artist_nfo_edit.save(artist, music, info, backups, {"genre": "Rock"}, profile.fingerprint)
    events = activity_store.artist_history(ALICE)
    event_id = next(iter(activity_store.nfo_changes_for([e.id for e in events])))
    edited = path.read_bytes()

    def no_space(*args):
        raise OSError("disk full")

    monkeypatch.setattr(artist_nfo_edit, "_keep", no_space)
    with pytest.raises(OSError, match="disk full"):
        artist_nfo_edit.restore(artist, music, info, backups, event_id)
    assert path.read_bytes() == edited
    assert activity_store.artist_history(ALICE) == events


def test_missing_backup_and_external_edits_explain_why_undo_is_unavailable(artist_client):
    from bs4 import BeautifulSoup

    client, cfg, _network = artist_client
    profile = client.get(f"/artist/{ALICE}").context["nfo"].profile
    client.post(f"/artist/{ALICE}/nfo", data={"expected": profile.fingerprint, "genre": "Rock"})
    page = client.get(f"/artist/{ALICE}")
    assert BeautifulSoup(page.text, "html.parser").find("button", string="Undo NFO") is not None
    (cfg.paths.config_dir / "nfoartists" / f"{profile.fingerprint}.nfo").unlink()
    page = client.get(f"/artist/{ALICE}")
    assert "original NFO backup is unavailable" in page.text
    assert BeautifulSoup(page.text, "html.parser").find("button", string="Undo NFO") is None
    profile.path.write_bytes(profile.path.read_bytes().replace(b"Rock", b"External"))
    page = client.get(f"/artist/{ALICE}?refresh=1")
    assert "NFO changed since this entry" in page.text


def test_previous_backup_directory_remains_usable_without_moving_or_deleting_it(local_nfo):
    artist, music, info, path, legacy, db = local_nfo
    original = path.read_bytes()
    profile = artist_nfo.read(path)
    artist_nfo_edit.save(artist, music, info, legacy, {"genre": "Rock"}, profile.fingerprint)
    events = activity_store.artist_history(ALICE)
    event_id = next(iter(activity_store.nfo_changes_for([e.id for e in events])))
    artist_nfo_edit.restore(artist, music, info, db.parent / "nfoartists", event_id)
    assert path.read_bytes() == original
    assert (legacy / f"{profile.fingerprint}.nfo").read_bytes() == original
