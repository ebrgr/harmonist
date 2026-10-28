"""Tag reads are served from memory while the file is unchanged (#904).

An album page used to read every file twice per view — once for the page, once
for the update plan — and every view read them all again. On a NAS with large
embedded artwork that was seconds of disk and hashing, under a placeholder that
blamed MusicBrainz.

What can go wrong with a cache like this is serving tags the file no longer
carries, so most of these tests are about the read coming back fresh.
"""

from __future__ import annotations

import os
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mutagen.mp4 import MP4

from harmonist import activity_store, formats, mb_cache
from harmonist import sidecar as sidecar_mod
from harmonist.config import BandcampConfig, Config, PathsConfig, ServerConfig, TestConfig
from harmonist.formats import m4a
from harmonist.formats.types import TrackTags
from harmonist.models import Sidecar
from harmonist.web.main import create_app

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SINE_M4A = FIXTURES_DIR / "sine.m4a"
MBID = "90490490-4444-5555-6666-777777777777"


class _CountingReads:
    """Wraps the M4A backend's `read_tags`, counting the files it opens."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.paths: list[Path] = []
        real = m4a.read_tags

        def counting(path: Path) -> TrackTags:
            self.paths.append(path)
            return real(path)

        monkeypatch.setattr(m4a, "read_tags", counting)


def _track(tmp_path: Path, title: str = "One") -> Path:
    f = tmp_path / "01 One.m4a"
    shutil.copy(SINE_M4A, f)
    a = MP4(f)
    a["\xa9nam"] = [title]
    a.save()
    return f


def test_an_unchanged_file_is_read_once(tmp_path, monkeypatch):
    f = _track(tmp_path)
    reads = _CountingReads(monkeypatch)

    assert formats.read_tags(f).title == "One"
    assert formats.read_tags(f).title == "One"

    assert reads.paths == [f]


def test_a_file_changed_by_another_tool_is_read_again(tmp_path, monkeypatch):
    f = _track(tmp_path)
    reads = _CountingReads(monkeypatch)
    assert formats.read_tags(f).title == "One"

    a = MP4(f)
    a["\xa9nam"] = ["Two"]
    a.save()

    assert formats.read_tags(f).title == "Two"
    assert reads.paths == [f, f]


def test_an_editor_that_restores_the_modification_time_is_still_noticed(tmp_path):
    """Mp3tag and friends offer to keep a file's mtime across a tag edit; the
    size needn't move either. ctime is what still says the file changed."""
    f = _track(tmp_path)
    before = f.stat()
    assert formats.read_tags(f).title == "One"

    a = MP4(f)
    a["\xa9nam"] = ["Two"]
    a.save()
    os.utime(f, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert (f.stat().st_size, f.stat().st_mtime_ns) == (before.st_size, before.st_mtime_ns)

    assert formats.read_tags(f).title == "Two"


def test_harmonists_own_write_is_never_followed_by_the_old_tags(tmp_path, monkeypatch):
    """Even where the file's stat can't tell the write happened — a filesystem
    whose timestamps are coarser than the read-then-write — so the write itself
    has to drop the entry."""
    f = _track(tmp_path)
    monkeypatch.setattr(formats, "_stamp", lambda path: (0,))
    assert formats.read_tags(f).title == "One"

    owned = formats.read_owned(f)
    owned["title"] = "Written"
    formats.write_owned(f, owned)

    assert formats.read_tags(f).title == "Written"


def test_a_caller_changing_what_it_was_given_changes_nothing_cached(tmp_path):
    """Either side: the read that filled the entry, and one served from it."""
    f = _track(tmp_path)
    for given in (formats.read_tags(f), formats.read_tags(f)):
        given.label.append("Mine")
        dict.__setitem__(given.owned, "title", "Mine")  # type: ignore[index]

    again = formats.read_tags(f)

    assert again.label == []
    assert again.owned["title"] == "One"


def test_an_unreadable_file_is_tried_again_next_time(tmp_path, monkeypatch):
    """A blinking network mount must not become a lasting "could not read"."""
    f = _track(tmp_path)
    calls: list[Path] = []

    def unreadable(path: Path) -> TrackTags:
        calls.append(path)
        return TrackTags(unreadable=True)

    monkeypatch.setattr(m4a, "read_tags", unreadable)

    formats.read_tags(f)
    formats.read_tags(f)

    assert calls == [f, f]


def test_a_file_that_has_gone_is_read_rather_than_served(tmp_path, monkeypatch):
    f = _track(tmp_path)
    formats.read_tags(f)
    f.unlink()

    assert formats.read_tags(f).unreadable


# --- the album page ----------------------------------------------------------


def _release() -> dict:
    return {
        "id": MBID,
        "title": "Test Album",
        "status": "Official",
        "artist-credit": [{"artist": {"id": "art-1", "name": "Artist"}, "name": "Artist"}],
        "release-group": {"id": "rg-1", "primary-type": "Album"},
        "medium-list": [
            {
                "position": "1",
                "format": "CD",
                "track-list": [
                    {
                        "id": f"rt-{i}",
                        "position": str(i),
                        "title": f"Track {i}",
                        "recording": {"id": f"rec-{i}", "title": f"Track {i}", "length": "1000"},
                    }
                    for i in range(1, 3)
                ],
            }
        ],
    }


@pytest.fixture
def unread_client(tmp_path, monkeypatch):
    """An identified album whose release has never been read from MusicBrainz."""
    monkeypatch.setattr("harmonist.cover_art.front_image", lambda *a, **kw: None)
    cfg = Config(
        paths=PathsConfig(config_dir=tmp_path / "config", music_dir=tmp_path / "music"),
        bandcamp=BandcampConfig(),
        server=ServerConfig(),
        test=TestConfig(mode="fixture"),
    )
    cfg.paths.config_dir.mkdir(parents=True)
    album = cfg.paths.music_dir / "Artist" / "Album"
    album.mkdir(parents=True)
    for i in (1, 2):
        f = album / f"0{i} Track {i}.m4a"
        shutil.copy(SINE_M4A, f)
        a = MP4(f)
        a["\xa9alb"] = ["Test Album"]
        a["\xa9ART"] = ["Artist"]
        a["trkn"] = [(i, 2)]
        a["----:com.apple.iTunes:MusicBrainz Album Id"] = [MBID.encode()]
        a.save()
    sidecar_mod.write(
        album, Sidecar(mb_release_id=MBID, tagged_at=datetime(2026, 1, 1, tzinfo=UTC))
    )
    return TestClient(create_app(cfg), headers={"HX-Request": "true"})


@pytest.fixture
def client(unread_client):
    """…and the same album with its release stored, as after any earlier view."""
    activity_store.store_release(
        MBID, mb_cache._key(mb_cache.mb_lookup.RELEASE_INCLUDES), _release()
    )
    return unread_client


def _album_id(client: TestClient) -> str:
    from harmonist import scanner

    music = client.app.state.cfg.paths.music_dir
    return scanner.scan(music)[0].id


def _placeholders(html: str) -> list[str]:
    """What the Album and Tracks sections say until /compare answers."""
    album = re.search(r'id="compare-[^"]*"[^>]*>\s*<p[^>]*>([^<]*)</p>', html)
    tracks = re.search(
        r'id="album-track-view"[^>]*>\s*<h2[^>]*>[^<]*</h2>\s*<p[^>]*>([^<]*)</p>', html
    )
    assert album is not None and tracks is not None
    return [album[1], tracks[1]]


def test_with_a_release_stored_the_page_waits_on_the_files(client):
    r = client.get(f"/album/{_album_id(client)}")

    assert r.status_code == 200
    assert _placeholders(r.text) == ["Reading tags…", "Reading tracks…"]


def test_with_nothing_stored_the_page_waits_on_musicbrainz(unread_client):
    r = unread_client.get(f"/album/{_album_id(unread_client)}")

    assert r.status_code == 200
    assert _placeholders(r.text) == [
        "Checking tags against MusicBrainz…",
        "Checking tracks against MusicBrainz…",
    ]


def test_the_album_comparison_reads_each_file_once(client, monkeypatch):
    album_id = _album_id(client)
    reads = _CountingReads(monkeypatch)

    r = client.get(f"/library/{album_id}/compare")

    assert r.status_code == 200
    assert "Test Album" in r.text
    assert sorted(p.name for p in reads.paths) == ["01 Track 1.m4a", "02 Track 2.m4a"]


def test_a_second_look_at_an_unchanged_album_reads_no_files(client, monkeypatch):
    album_id = _album_id(client)
    assert client.get(f"/library/{album_id}/compare").status_code == 200
    reads = _CountingReads(monkeypatch)

    r = client.get(f"/library/{album_id}/compare")

    assert r.status_code == 200
    assert "Test Album" in r.text
    assert reads.paths == []
