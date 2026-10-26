"""NFO placement is a reviewed choice, never inferred directory ownership."""

from dataclasses import replace

import pytest

from harmonist import artist_nfo, artist_nfo_edit, artists
from harmonist.models import Album, AlbumArtist, AlbumState
from test.test_artist_web import ALICE, BOB


def album(root, relative, key, artist_id=ALICE):
    path = root / relative
    path.mkdir(parents=True)
    (path / "track.flac").write_bytes(b"audio untouched")
    return Album(
        id=key,
        path=path,
        title=key,
        artist="Alice",
        track_count=1,
        state=AlbumState.COMPLETE,
        artist_credits=(AlbumArtist("Alice", artist_id),),
    )


@pytest.fixture
def tree(tmp_path):
    music = tmp_path / "music"
    albums = [album(music, "Alice/One", "one"), album(music, "Alice/Two", "two")]
    artist = artists.build_index(albums)[0]
    return music, albums, artist, tmp_path / "info"


def plan(tree, preference="info_preferred", info=True):
    music, albums, artist, directory = tree
    return artist_nfo_edit.destinations(
        artist, None, music, directory if info else None, albums, preference
    )


def test_preferences_choose_one_target_and_work_without_information_root(tree):
    music, _, _, info = tree
    external = plan(tree)
    assert external.default.path == info / "Alice/artist.nfo"
    audio = plan(tree, "audio_preferred")
    assert audio.default.path == music / "Alice/artist.nfo"
    assert len(audio.options) == 2
    assert plan(tree, info=False).default.path == music / "Alice/artist.nfo"
    assert not info.exists()


def test_snapshot_includes_other_and_unknown_artists_and_unscanned_audio(tree):
    music, albums, _, _ = tree
    albums.append(album(music, "Alice/Unknown", "unknown", None))
    assert not plan(tree, info=False).options
    albums.pop()
    assert not plan(tree, info=False).options  # unknown audio remains on disk
    (music / "Alice/Unknown/track.flac").unlink()
    albums.append(album(music, "Alice/Other", "other", BOB))
    assert not plan(tree, info=False).options


def test_single_and_multidisc_albums_need_explicit_choice(tmp_path):
    music = tmp_path / "music"
    part = album(music, "Alice/One/CD1", "one")
    second = album(music, "Alice/One/CD2", "two")
    merged = replace(part, paths=(part.path, second.path), track_count=2)
    artist = artists.build_index([merged])[0]
    result = artist_nfo_edit.destinations(artist, None, music, None, [merged])
    assert [d.path for d in result.options] == [music / "Alice/artist.nfo"]
    assert result.default is None


def test_ambiguous_split_folders_require_choice_and_foreign_nfo_is_excluded(tree):
    music, albums, artist, info = tree
    split = album(music, "Elsewhere/Alice/Three", "three")
    albums.append(split)
    artist = replace(artist, albums=tuple(albums))
    result = artist_nfo_edit.destinations(artist, None, music, None, albums)
    assert len(result.options) == 2 and result.default is None
    path = music / "Alice/artist.nfo"
    path.write_text(f"<artist><musicBrainzArtistID>{BOB}</musicBrainzArtistID></artist>")
    result = artist_nfo_edit.destinations(artist, None, music, info, albums)
    assert path not in [d.path for d in result.options]


def test_selected_target_cannot_silently_change_or_accept_a_path(tree, tmp_path):
    music, albums, artist, info = tree
    destination = plan(tree, "audio_preferred").default
    assert destination is not None
    albums.append(album(music, "Alice/Other", "other", BOB))
    with pytest.raises(artist_nfo_edit.NfoChangedError, match="destination"):
        artist_nfo_edit.save(
            artist,
            music,
            info,
            tmp_path / "backups",
            {"genre": "Rock"},
            "",
            albums=albums,
            destination=destination.key,
        )
    with pytest.raises(artist_nfo_edit.NfoChangedError, match="destination"):
        artist_nfo_edit.save(
            artist,
            music,
            info,
            tmp_path / "backups",
            {},
            "",
            albums=albums,
            destination=str(tmp_path / "arbitrary/artist.nfo"),
        )
    assert not list(music.rglob("artist.nfo")) and not info.exists()


def test_audio_creation_and_edit_use_one_file_and_existing_history(tree, tmp_path):
    from harmonist import activity_store

    music, albums, artist, info = tree
    activity_store.init(tmp_path / "activity.db")
    backup = tmp_path / "nfoartists"
    choice = plan(tree, "audio_preferred").default
    created = artist_nfo_edit.save(
        artist,
        music,
        info,
        backup,
        {"genre": "Rock"},
        "",
        albums=albums,
        destination=choice.key,
    )
    assert created.path == music / "Alice/artist.nfo"
    assert created.created and not info.exists()
    profile = artist_nfo.read(created.path)
    original = created.path.read_bytes()
    existing = artist_nfo_edit.destinations(artist, profile, music, info, albums)
    assert len(existing.options) == 1 and existing.default.path == created.path
    saved = artist_nfo_edit.save(
        artist,
        music,
        info,
        backup,
        {"genre": "Jazz"},
        profile.fingerprint,
        albums=albums,
        destination=existing.default.key,
    )
    assert saved.changed and not info.exists()
    assert (backup / f"{profile.fingerprint}.nfo").read_bytes() == original
    changes = activity_store.nfo_changes_for([e.id for e in activity_store.artist_history(ALICE)])
    latest = max(changes.values(), key=lambda c: c.event_id)
    assert latest.file == str(created.path)
    artist_nfo_edit.restore(artist, music, info, backup, latest.event_id)
    assert created.path.read_bytes() == original


def test_symlink_and_music_root_are_never_audio_destinations(tree, tmp_path):
    music, albums, _artist, _ = tree
    outside = tmp_path / "outside"
    outside.mkdir()
    (music / "Alice/artist.nfo").symlink_to(outside / "new.nfo")
    assert not plan(tree, info=False).options
    flat = album(music, "FlatAlbum", "flat")
    flat_artist = artists.build_index([flat])[0]
    assert not artist_nfo_edit.destinations(flat_artist, None, music, None, [*albums, flat]).options


def test_source_collision_does_not_turn_a_generic_ancestor_into_a_destination(tree):
    music, albums, artist, _ = tree
    container = music / "Genre"
    container.mkdir()
    (music / "Alice").rename(container / "Alice")
    moved = [replace(a, path=container / "Alice" / a.path.name) for a in albums]
    artist = replace(artist, albums=tuple(moved))
    (container / "Alice/artist.nfo").write_text("<artist><name>Other</name></artist>")
    assert not artist_nfo_edit.destinations(artist, None, music, None, moved).options


def test_permission_failure_never_switches_target_or_records_success(tree, tmp_path, monkeypatch):
    from harmonist import activity_store

    music, albums, artist, info = tree
    activity_store.init(tmp_path / "activity.db")
    choice = plan(tree, "audio_preferred").default

    def denied(*args, **kwargs):
        raise PermissionError("read-only audio folder")

    monkeypatch.setattr(artist_nfo_edit, "_atomic_write", denied)
    with pytest.raises(PermissionError, match="read-only"):
        artist_nfo_edit.save(
            artist,
            music,
            info,
            tmp_path / "backups",
            {"genre": "Rock"},
            "",
            albums=albums,
            destination=choice.key,
        )
    assert not info.exists() and not list(music.rglob("artist.nfo"))
    events = activity_store.artist_history(artist.key)
    assert events and not activity_store.nfo_changes_for([e.id for e in events])


def test_destination_fingerprint_detects_changed_files_and_directory_replacement(tree):
    music, albums, artist, info = tree
    before = plan(tree, "audio_preferred").default.key
    (music / "Alice/One/track.flac").write_bytes(b"Changed tags or audio")
    after = plan(tree, "audio_preferred").default.key
    assert before != after
    (music / "Alice").rename(music / "Old Alice")
    (music / "Alice").mkdir()
    assert before not in {
        d.key for d in artist_nfo_edit.destinations(artist, None, music, info, albums).options
    }


def test_root_alias_is_allowed_but_links_inside_the_library_are_not(tree, tmp_path):
    music, albums, artist, _ = tree
    alias = tmp_path / "root-alias"
    alias.symlink_to(music, target_is_directory=True)
    aliased_albums = [replace(a, path=alias / a.path.relative_to(music)) for a in albums]
    aliased_artist = replace(artist, albums=tuple(aliased_albums))
    result = artist_nfo_edit.destinations(aliased_artist, None, alias, None, aliased_albums)
    assert result.default is not None
    assert result.default.path == music / "Alice/artist.nfo"
    (music / "Alias Artist").symlink_to(music / "Alice", target_is_directory=True)
    linked_albums = [replace(a, path=music / "Alias Artist" / a.path.name) for a in albums]
    linked_artist = replace(artist, albums=tuple(linked_albums))
    assert not artist_nfo_edit.destinations(linked_artist, None, music, None, linked_albums).options


def test_one_logical_album_split_between_audio_branches_offers_one_choice_per_branch(tmp_path):
    music = tmp_path / "music"
    first = album(music, "Collection One/Alice/Disc 1", "release")
    second = album(music, "Collection Two/Alice/Disc 2", "other-part")
    merged = replace(first, paths=(first.path, second.path), track_count=2)
    artist = artists.build_index([merged])[0]
    result = artist_nfo_edit.destinations(artist, None, music, None, [merged])
    assert {d.path for d in result.options} == {
        music / "Collection One/Alice/artist.nfo",
        music / "Collection Two/Alice/artist.nfo",
    }
    assert result.default is None
