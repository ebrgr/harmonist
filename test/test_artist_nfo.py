"""Read existing artist NFOs without directory/name-based identity guesses."""

import os
from dataclasses import replace

import pytest

from harmonist import artist_nfo
from harmonist.artists import Artist
from harmonist.models import Album, AlbumState

ALICE = "11111111-1111-4111-8111-111111111111"
BOB = "22222222-2222-4222-8222-222222222222"


@pytest.mark.parametrize("filename", ["artist.jpg", "artist.png"])
def test_artist_picture_without_a_thumb_reference(tmp_path, filename):
    path = tmp_path / "artist.nfo"
    path.write_text(f"<artist><musicbrainzartistid>{ALICE}</musicbrainzartistid></artist>")
    image = tmp_path / filename
    image.write_bytes(b"local-artist-image")
    assert artist_nfo.local_image(artist_nfo.read(path)) == image


@pytest.mark.parametrize("id_tag", ["musicBrainzArtistID", "musicbrainzartistid"])
def test_parser_aliases_encoding_and_safe_local_art(tmp_path, id_tag):
    path = tmp_path / "artist.nfo"
    path.write_bytes(
        (
            f'<?xml version="1.0" encoding="iso-8859-1"?>'
            f"<artist><name>André</name><{id_tag}>{ALICE}</{id_tag}>"
            "<biography>&lt;script&gt;no&lt;/script&gt;</biography><genre>Jazz</genre>"
            "<genre>Soul</genre><thumb>folder.jpg</thumb></artist>"
        ).encode("latin1")
    )
    image = tmp_path / "folder.jpg"
    image.write_bytes(b"local-image")
    profile = artist_nfo.read(path)
    assert (profile.name, profile.mbid) == ("André", ALICE)
    assert profile.biography == "<script>no</script>"
    assert ("Genre", "Jazz · Soul") in profile.facts
    assert artist_nfo.local_image(profile) == image
    # The same metadata file changed externally: the cached parse must change.
    path.write_text(
        f"<artist><musicbrainzartistid>{ALICE}</musicbrainzartistid>"
        "<name>New name</name><thumb>https://example.org/photo.jpg</thumb></artist>"
    )
    image.unlink()
    assert artist_nfo.read(path).name == "New name"
    assert artist_nfo.local_image(artist_nfo.read(path)) is None


@pytest.mark.parametrize(
    "payload",
    [
        b"<artist>",
        b"<album/>",
        b'<!DOCTYPE artist [<!ENTITY x SYSTEM "file:///etc/passwd">]><artist>&x;</artist>',
        "<!DOCTYPE artist><artist/>".encode("utf-16"),
        b"<artist>" + b"x" * (1024 * 1024) + b"</artist>",
    ],
)
def test_invalid_xml_is_an_error_not_missing_metadata(tmp_path, payload):
    path = tmp_path / "artist.nfo"
    path.write_bytes(payload)
    with pytest.raises(artist_nfo.NfoError):
        artist_nfo.read(path)


def test_information_folder_supports_collaboration_and_rejects_ambiguity(tmp_path):
    music = tmp_path / "music"
    folder = music / "arbitrary" / "layout" / "release"
    folder.mkdir(parents=True)
    album = Album(
        id="together",
        path=folder,
        title="Together",
        artist="Alice & Bob",
        track_count=1,
        state=AlbumState.COMPLETE,
        paths=(folder,),
    )
    alice = Artist(ALICE, "Same name", ALICE, None, (album,))
    info = tmp_path / "ArtistInfo"
    for name, mbid in [("one", ALICE), ("two", BOB)]:
        d = info / name
        d.mkdir(parents=True)
        (d / "artist.nfo").write_text(
            f"<artist><name>Same name</name><musicBrainzArtistID>{mbid}</musicBrainzArtistID></artist>"
        )
    original = (info / "one" / "artist.nfo").read_bytes()
    assert artist_nfo.for_artist(alice, music, info).profile.path == info / "one" / "artist.nfo"
    bob = replace(alice, key=BOB, mbid=BOB)
    assert artist_nfo.for_artist(bob, music, info).profile.path == info / "two" / "artist.nfo"
    # A matching display name without a confirmed ID does not establish identity.
    assert artist_nfo.for_artist(replace(alice, mbid=None), music, info).profile is None
    local = music / "arbitrary" / "artist.nfo"
    local.write_bytes(original)
    ambiguous = artist_nfo.for_artist(alice, music, info)
    assert ambiguous.profile is None
    assert ambiguous.errors
    assert set(ambiguous.candidates) == {local, info / "one" / "artist.nfo"}
    selected = artist_nfo.for_artist(alice, music, info, artist_nfo.selection_key(local))
    assert selected.profile is not None
    assert selected.profile.path == local
    local.unlink()
    assert artist_nfo.for_artist(alice, music, info).profile is not None
    assert (info / "one" / "artist.nfo").read_bytes() == original


@pytest.mark.parametrize("kind", ["relative", "absolute", "symlink", "remote"])
def test_image_references_do_not_escape_nfo_directory(tmp_path, kind):
    directory = tmp_path / "artist"
    directory.mkdir()
    outside = tmp_path / "private.jpg"
    outside.write_bytes(b"private-image")
    (directory / "linked.jpg").symlink_to(outside)
    reference = {
        "relative": "../private.jpg",
        "absolute": str(outside),
        "symlink": "linked.jpg",
        "remote": "https://example.org/a.jpg",
    }[kind]
    path = directory / "artist.nfo"
    path.write_text(f"<artist><thumb>{reference}</thumb></artist>")
    assert artist_nfo.local_image(artist_nfo.read(path)) is None


def test_read_again_bypasses_an_unchanged_file_timestamp(tmp_path):
    info = tmp_path / "info"
    info.mkdir()
    path = info / "artist.nfo"
    template = (
        f"<artist><musicbrainzartistid>{ALICE}</musicbrainzartistid><name>{{}}</name></artist>"
    )
    path.write_text(template.format("Alice"))
    artist = Artist(ALICE, "Alice", ALICE, None, ())
    original = path.stat()
    assert artist_nfo.for_artist(artist, tmp_path, info).profile.name == "Alice"
    path.write_text(template.format("Other"))
    os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
    assert artist_nfo.for_artist(artist, tmp_path, info, refresh=True).profile.name == "Other"


def test_unreadable_and_missing_information_folders_are_visible_errors(tmp_path):
    artist = Artist(ALICE, "Alice", ALICE, None, ())
    result = artist_nfo.for_artist(artist, tmp_path, tmp_path / "missing")
    assert result.profile is None
    assert result.errors


@pytest.mark.parametrize("location", ["album", "artist", "library"])
def test_missing_information_folder_keeps_local_nfos_and_recovers(tmp_path, caplog, location):
    music = tmp_path / "music"
    folder = music / "arbitrary artist folder" / "album"
    folder.mkdir(parents=True)
    album = Album(
        id="local",
        path=folder,
        title="Local album",
        artist="Alice",
        track_count=1,
        state=AlbumState.COMPLETE,
    )
    artist = Artist(ALICE, "Alice", ALICE, None, (album,))
    directory = {"album": folder, "artist": folder.parent, "library": music}[location]
    local = directory / "artist.nfo"
    local.write_text(
        f"<artist><musicbrainzartistid>{ALICE}</musicbrainzartistid>"
        "<biography>Local biography</biography></artist>"
    )
    info = tmp_path / "missing information folder"
    result = artist_nfo.for_artist(artist, music, info)
    assert result.profile is not None
    assert result.profile.path == local
    assert result.profile.biography == "Local biography"
    assert result.candidates == (local,)
    assert any(str(info) in error for error in result.errors)
    assert any(
        record.levelname == "WARNING" and str(info) in record.message for record in caplog.records
    )
    assert not info.exists()
    # Recovery is discovered on the next visit; the fallback must not disable
    # the external source or guess between two matching NFOs once it exists.
    external = info / "Alice"
    external.mkdir(parents=True)
    (external / "artist.nfo").write_bytes(local.read_bytes())
    recovered = artist_nfo.for_artist(artist, music, info)
    assert recovered.profile is None
    assert set(recovered.candidates) == {local, external / "artist.nfo"}
    assert not any(str(info) in error for error in recovered.errors)


def test_permission_failure_in_information_folder_remains_a_visible_error(tmp_path, monkeypatch):
    from pathlib import Path

    info = tmp_path / "private"
    info.mkdir()
    (tmp_path / "artist.nfo").write_text(
        f"<artist><musicbrainzartistid>{ALICE}</musicbrainzartistid></artist>"
    )
    album = Album(
        id="local",
        path=tmp_path,
        title="Local album",
        artist="Alice",
        track_count=1,
        state=AlbumState.COMPLETE,
    )
    artist = Artist(ALICE, "Alice", ALICE, None, (album,))
    original = Path.iterdir

    def denied(path):
        if path == info:
            raise PermissionError("Permission denied for artist information folder")
        return original(path)

    monkeypatch.setattr(Path, "iterdir", denied)
    result = artist_nfo.for_artist(artist, tmp_path, info)
    assert result.profile is None
    assert any("Permission denied" in error for error in result.errors)


def test_profile_keeps_genres_and_nfo_discography_separate(tmp_path):
    path = tmp_path / "artist.nfo"
    path.write_text(
        f"<artist><musicBrainzArtistID>{ALICE}</musicBrainzArtistID>"
        "<genre>Rock</genre><genre>Alternative</genre><genre>Rock</genre>"
        "<album><title>Unowned album</title><year>1999</year>"
        f"<musicbrainzreleasegroupid>{BOB}</musicbrainzreleasegroupid></album>"
        "<album><title>&lt;script&gt;title&lt;/script&gt;</title><year>2002</year>"
        "<musicbrainzreleasegroupid>not-an-id</musicbrainzreleasegroupid></album>"
        "<album><year>2000</year></album></artist>"
    )
    profile = artist_nfo.read(path)
    assert getattr(profile, "genres", ()) == ("Rock", "Alternative")
    assert [(a.title, a.year, a.release_group_id) for a in profile.discography] == [
        ("Unowned album", "1999", BOB),
        ("<script>title</script>", "2002", None),
    ]
