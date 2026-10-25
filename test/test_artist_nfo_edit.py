"""Explicit edits preserve the NFO around them and never guess their target."""

import sqlite3
from dataclasses import replace

import pytest

from harmonist import activity_store, artist_nfo, artist_nfo_edit
from harmonist.artists import Artist
from harmonist.models import Album, AlbumState
from test.test_artist_web import ALICE, BOB


@pytest.fixture
def local_nfo(tmp_path):
    music = tmp_path / "music"
    music.mkdir()
    info = tmp_path / "info"
    source = info / "arbitrary folder" / "artist.nfo"
    source.parent.mkdir(parents=True)
    source.write_bytes(
        (
            '<?xml version="1.0" encoding="iso-8859-1"?>'
            '<artist custom="keep"><!--keep this comment--><?source keep?>'
            f"<musicBrainzArtistID>{ALICE}</musicBrainzArtistID>"
            "<name>André</name><sortName>André, Artist</sortName><genre>Jazz</genre>"
            "<biography>Original biography</biography><thumb aspect='artist'>artist.jpg</thumb>"
            "<unknown flag='yes'><child>Untouched</child></unknown>"
            "<album><title>Discography album</title><year>2001</year></album></artist>"
        ).encode("latin1")
    )
    album = Album(
        id="local",
        path=music,
        title="Local",
        artist="André",
        track_count=1,
        state=AlbumState.COMPLETE,
    )
    artist = Artist(ALICE, "André", ALICE, None, (album,))
    db = tmp_path / "config" / "activity.db"
    activity_store.init(db)
    return artist, music, info, source, tmp_path / "config" / "artist-nfo-backups", db


def test_edit_preserves_other_xml_and_backs_up_exact_bytes_and_is_idempotent(local_nfo):
    artist, music, info, source, backups, db = local_nfo
    original = source.read_bytes()
    profile = artist_nfo.read(source)
    values = {"name": "André edited", "genre": "Jazz\nRock\nJazz", "biography": "New biography"}
    with activity_store.action():
        saved = artist_nfo_edit.save(artist, music, info, backups, values, profile.fingerprint)
    assert saved.changed and not saved.created
    result = artist_nfo.read(source)
    assert result.name == "André edited"
    assert result.genres == ("Jazz", "Rock")
    assert result.mbid == ALICE
    assert result.sort_name == "André, Artist"
    assert result.discography[0].title == "Discography album"
    content = source.read_text()
    assert '<artist custom="keep">' in content
    assert '<unknown flag="yes"><child>Untouched</child></unknown>' in content
    assert "<!--keep this comment-->" in content
    assert "<?source keep?>" in content
    assert '<thumb aspect="artist">artist.jpg</thumb>' in content
    assert (backups / f"{profile.fingerprint}.nfo").read_bytes() == original
    before = source.stat().st_mtime_ns
    repeated = artist_nfo_edit.save(artist, music, info, backups, values, profile.fingerprint)
    assert not repeated.changed
    assert source.stat().st_mtime_ns == before
    assert len(list(backups.iterdir())) == 1
    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT message, action_id FROM events WHERE source='audit'").fetchall()
    assert len(rows) == 2
    assert rows[0][0].startswith("artist_nfo.write ")
    assert str(backups) in rows[0][0]
    assert rows[1][0].startswith("artist_nfo.saved ")
    assert rows[0][1] is not None and rows[0][1] == rows[1][1]


def test_stale_draft_preserves_external_edit(local_nfo):
    artist, music, info, source, backups, _db = local_nfo
    profile = artist_nfo.read(source)
    source.write_bytes(source.read_bytes().replace(b"Original biography", b"External biography"))
    changed = source.read_bytes()
    with pytest.raises(artist_nfo_edit.NfoChangedError, match="changed"):
        artist_nfo_edit.save(
            artist, music, info, backups, {"biography": "Draft"}, profile.fingerprint
        )
    assert source.read_bytes() == changed
    assert not backups.exists()


def test_create_uses_configured_information_folder_and_fixed_artist_identity(local_nfo):
    artist, music, info, _source, backups, _db = local_nfo
    missing = info.parent / "not created yet"
    bob = replace(artist, key=BOB, mbid=BOB, name="Bob / unsafe folder name")
    created = artist_nfo_edit.save(bob, music, missing, backups, {"genre": "Rock"}, "")
    assert created.path == missing / BOB / "artist.nfo"
    assert created.created
    profile = artist_nfo.for_artist(bob, music, missing).profile
    assert profile is not None and profile.mbid == BOB
    assert profile.name == bob.name
    assert profile.genres == ("Rock",)
    assert not backups.exists()
    assert not artist_nfo_edit.save(bob, music, missing, backups, {"genre": "Rock"}, "").changed
    with pytest.raises(artist_nfo.NfoError, match="MusicBrainz"):
        artist_nfo_edit.save(replace(bob, mbid=None), music, missing, backups, {}, "")


def test_creation_cannot_follow_artist_directory_outside_information_root(local_nfo, tmp_path):
    artist, _music, info, _source, _backups, _db = local_nfo
    outside = tmp_path / "outside"
    outside.mkdir()
    (info / BOB).symlink_to(outside, target_is_directory=True)
    with pytest.raises(artist_nfo.NfoError, match="outside"):
        artist_nfo_edit.target(replace(artist, mbid=BOB), None, info)
    assert not list(outside.iterdir())


@pytest.mark.parametrize("fail_backup", [True, False])
def test_write_failure_preserves_original_and_audits_attempt_only_after_backup(
    local_nfo, monkeypatch, fail_backup
):
    artist, music, info, source, backups, db = local_nfo
    original = source.read_bytes()
    profile = artist_nfo.read(source)
    write = artist_nfo_edit._atomic_write

    def fail(path, data, mode):
        if fail_backup or path == source:
            raise OSError("disk full")
        write(path, data, mode)

    monkeypatch.setattr(artist_nfo_edit, "_atomic_write", fail)
    with pytest.raises(OSError, match="disk full"):
        artist_nfo_edit.save(artist, music, info, backups, {"name": "Draft"}, profile.fingerprint)
    assert source.read_bytes() == original
    with sqlite3.connect(db) as conn:
        messages = [r[0] for r in conn.execute("SELECT message FROM events")]
    assert any(m.startswith("artist_nfo.write ") for m in messages) == (not fail_backup)
    assert not any(m.startswith("artist_nfo.saved ") for m in messages)
    if not fail_backup:
        assert (backups / f"{profile.fingerprint}.nfo").read_bytes() == original


def test_ambiguous_sources_require_selection_and_cannot_overwrite_an_unmatched_file(local_nfo):
    artist, music, info, source, backups, _db = local_nfo
    original = source.read_bytes()
    local = music / "artist.nfo"
    local.write_bytes(original)
    with pytest.raises(artist_nfo_edit.NfoChangedError, match="source"):
        artist_nfo_edit.save(
            artist, music, info, backups, {"name": "Draft"}, artist_nfo.read(source).fingerprint
        )
    assert source.read_bytes() == local.read_bytes() == original
    target = info / BOB / "artist.nfo"
    target.parent.mkdir()
    target.write_bytes(original)
    with pytest.raises(artist_nfo_edit.NfoChangedError, match="already exists"):
        artist_nfo_edit.save(replace(artist, mbid=BOB), music, info, backups, {"name": "Bob"}, "")
    assert target.read_bytes() == original


def test_creation_does_not_replace_a_concurrently_created_file(local_nfo, monkeypatch):
    artist, music, info, _source, backups, _db = local_nfo
    other = replace(artist, key=BOB, mbid=BOB)
    target = info / BOB / "artist.nfo"
    original_link = artist_nfo_edit.os.link

    def created_elsewhere(source, destination):
        target.write_bytes(b"File created by another process")
        return original_link(source, destination)

    monkeypatch.setattr(artist_nfo_edit.os, "link", created_elsewhere)
    with pytest.raises(FileExistsError):
        artist_nfo_edit.save(other, music, info, backups, {"genre": "Rock"}, "")
    assert target.read_bytes() == b"File created by another process"


def test_edited_fields_cannot_introduce_invalid_xml(local_nfo):
    artist, music, info, source, backups, _db = local_nfo
    original = source.read_bytes()
    with pytest.raises(artist_nfo.NfoError, match="characters XML"):
        artist_nfo_edit.save(
            artist,
            music,
            info,
            backups,
            {"biography": "Bad\x00value"},
            artist_nfo.read(source).fingerprint,
        )
    assert source.read_bytes() == original
    assert not backups.exists()
