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
    bob = replace(artist, key=BOB, mbid=BOB, name="Bób & Friends")
    created = artist_nfo_edit.save(bob, music, missing, backups, {"genre": "Rock"}, "")
    assert created.path == missing / "Bób & Friends" / "artist.nfo"
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
    (info / "André").symlink_to(outside, target_is_directory=True)
    with pytest.raises(artist_nfo.NfoError, match="outside"):
        artist_nfo_edit.target(replace(artist, mbid=BOB), None, info)
    assert not list(outside.iterdir())


@pytest.mark.parametrize(
    "name,folder",
    [
        ("AC/DC", "ACDC"),
        ("Batman: Begins", "Batman Begins"),
        ("Artist\\Name", "ArtistName"),
        ('A < B > C | D * E ? F "G"', "A B C D E F G"),
        (".  André\t &\n Friends ...", "André & Friends"),
        ("NUL", "_NUL"),
        ("con", "_con"),
        ("COM1", "_COM1"),
        ("LPT9.txt", "_LPT9.txt"),
        ("COM10", "COM10"),
        ("../", "Noname Artist"),
        ("", "Noname Artist"),
    ],
)
def test_creation_sanitizes_only_the_folder_and_preserves_identity(local_nfo, name, folder):
    artist, music, info, _source, backups, _db = local_nfo
    other = replace(artist, key=BOB, mbid=BOB, name=name)
    saved = artist_nfo_edit.save(other, music, info, backups, {"genre": "Rock"}, "")
    assert saved.created and saved.path == info / folder / "artist.nfo"
    profile = artist_nfo.for_artist(other, music, info).profile
    assert profile is not None
    assert profile.mbid == BOB and profile.name == name.strip()
    assert profile.genres == ("Rock",)
    assert not artist_nfo_edit.save(other, music, info, backups, {"genre": "Rock"}, "").changed


@pytest.mark.parametrize("name", ["Name\x00", "Name\ud800", "a" * 256])
def test_creation_refuses_unrepresentable_folder_names(local_nfo, name):
    artist, music, info, _source, backups, _db = local_nfo
    before = {p: p.read_bytes() for p in info.rglob("*") if p.is_file()}
    with pytest.raises(artist_nfo.NfoError, match="artist name"):
        artist_nfo_edit.save(
            replace(artist, key=BOB, mbid=BOB, name=name),
            music,
            info,
            backups,
            {"genre": "Rock"},
            "",
        )
    assert {p: p.read_bytes() for p in info.rglob("*") if p.is_file()} == before


def test_sanitized_folder_collision_never_overwrites_another_artist(local_nfo):
    artist, music, info, _source, backups, _db = local_nfo
    separate = info.parent / "sanitized"
    first = replace(artist, name="AC/DC")
    saved = artist_nfo_edit.save(first, music, separate, backups, {"genre": "Rock"}, "")
    original = saved.path.read_bytes()
    with pytest.raises(artist_nfo_edit.NfoChangedError, match="already exists"):
        artist_nfo_edit.save(
            replace(artist, key=BOB, mbid=BOB, name="ACDC"),
            music,
            separate,
            backups,
            {"genre": "Jazz"},
            "",
        )
    assert saved.path.read_bytes() == original
    assert artist_nfo.read(saved.path).mbid == ALICE
    assert list(separate.iterdir()) == [separate / "ACDC"]


def test_creation_refuses_case_insensitive_folder_collisions(local_nfo):
    artist, music, info, _source, backups, _db = local_nfo
    other = info / "BOB"
    other.mkdir()
    original = b"<artist><name>Another Bob</name></artist>"
    (other / "artist.nfo").write_bytes(original)
    with pytest.raises(artist_nfo.NfoError, match="folder.*already exists"):
        artist_nfo_edit.save(
            replace(artist, key=BOB, mbid=BOB, name="Bob"),
            music,
            info,
            backups,
            {"genre": "Rock"},
            "",
        )
    assert (other / "artist.nfo").read_bytes() == original
    assert sorted(p.name for p in info.iterdir()) == ["BOB", "arbitrary folder"]


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
    target = info / "André" / "artist.nfo"
    target.parent.mkdir()
    target.write_bytes(original)
    with pytest.raises(artist_nfo_edit.NfoChangedError, match="already exists"):
        artist_nfo_edit.save(replace(artist, mbid=BOB), music, info, backups, {"name": "Bob"}, "")
    assert target.read_bytes() == original


def test_creation_does_not_replace_a_concurrently_created_file(local_nfo, monkeypatch):
    artist, music, info, _source, backups, _db = local_nfo
    other = replace(artist, key=BOB, mbid=BOB)
    target = info / "André" / "artist.nfo"
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
