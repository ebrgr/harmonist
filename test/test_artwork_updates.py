"""Artwork discovery uses all local carriers, and never the tag-update verdict."""

import logging
import shutil
import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from harmonist import (
    activity,
    activity_store,
    artwork,
    artwork_updates,
    caa_cache,
    cover_art,
    formats,
    mb_lookup,
    scanner,
)
from harmonist.models import BorrowedArtwork
from harmonist.web.main import _library_filter, _library_filters, _library_page_vars
from test.test_artwork import png_bytes
from test.test_gardener import _release, _stub_app, _stub_runner, _tagged

MBID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


@pytest.fixture(autouse=True)
def cache(tmp_path):
    activity_store.init(tmp_path / "activity.db")
    cover_art.configure_cache(tmp_path / "caa")
    caa_cache.configure(timedelta(days=7))
    yield
    cover_art.configure_cache(None)


def _archive(data, *, source="release", age=timedelta()):
    image = formats.EmbeddedArt.of(data, "image/png")
    answer = activity_store.CachedCoverArt(
        fetched_at=datetime.now(UTC) - age,
        image_url="https://example.test/front.png",
        width=image.size.width if image.size else None,
        height=image.size.height if image.size else None,
        mime="image/png",
        source=source,
    )
    cover_art.cache_image(MBID, data, "image/png")
    activity_store.store_cover_art(MBID, answer)
    return answer


def test_no_artwork_checks_later_tracks(tmp_path):
    folder = tmp_path / "Album"
    folder.mkdir()
    for name in ("01.m4a", "02.m4a"):
        shutil.copy(Path(__file__).parent / "fixtures/sine.m4a", folder / name)
    formats.write_cover(folder / "02.m4a", png_bytes(64, 64))
    album = scanner.scan(tmp_path)[0]
    assert not _library_filters({})["no-artwork"][1](album)


def test_unreadable_is_not_confirmed_missing_artwork(tmp_path):
    folder = tmp_path / "Album"
    folder.mkdir()
    (folder / "01.m4a").write_bytes(b"broken audio")
    album = scanner.scan(tmp_path)[0]
    assert not _library_filters({})["no-artwork"][1](album)


@pytest.mark.parametrize(
    ("images", "folder_cover", "unreadable", "expected"),
    [
        ((None, None), False, False, {"no-artwork", "artwork-missing-tracks"}),
        ((None, None), True, False, {"artwork-missing-tracks"}),
        ((64, None), False, False, {"artwork-missing-tracks"}),
        ((64, 64), False, False, set()),
        ((64, 65), False, False, {"artwork-different-tracks"}),
        ((64, 65, None), True, False, {"artwork-missing-tracks", "artwork-different-tracks"}),
        ((None, None), False, True, set()),
        ((64, 65), False, True, set()),
    ],
)
def test_local_observation_filters(tmp_path, images, folder_cover, unreadable, expected):
    album = _tagged(tmp_path / "music", _release(mbid=MBID, tracks=len(images)), tracks=len(images))
    for path, size in zip(sorted(album.path.glob("*.m4a")), images, strict=True):
        if size:
            formats.write_cover(path, png_bytes(size, size))
    if folder_cover:
        (album.path / "cover.png").write_bytes(png_bytes(70, 70))
    album = scanner.scan(tmp_path / "music")[0]
    assert album.artwork_local is not None
    album.artwork_local = replace(album.artwork_local, unreadable=unreadable)
    filters = _library_filters({})
    slugs = {"no-artwork", "artwork-missing-tracks", "artwork-different-tracks"}
    assert {slug for slug in slugs if filters[slug][1](album)} == expected
    album.artwork_local = None
    assert not any(filters[slug][1](album) for slug in slugs)


def test_observations_intersect_updates_and_clear_independently(tmp_path, monkeypatch):
    album = _tagged(tmp_path / "music", _release(mbid=MBID))
    album.update_available = True
    album.update_significance = "structure"
    _archive(png_bytes(120, 120))
    artwork_updates.warm([album])
    other = replace(album, id="other", title="Other", update_available=False)

    def forbidden(*args, **kwargs):
        pytest.fail("Filtering read disk or used the network")

    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr(cover_art, "check_front", forbidden)
    query = "no-artwork,artwork-addition,update-structure"
    assert _library_filter(query) == query
    view = _library_page_vars([album, other], 1, 20, filter_=query)
    assert view["rows"] == [album]
    assert view["clear_facets"]["artwork"] == "artwork-addition,update-structure"
    assert view["clear_facets"]["artwork_updates"] == "no-artwork,update-structure"
    assert view["clear_facets"]["updates"] == "no-artwork,artwork-addition"
    updates = {f["slug"]: f for f in view["artwork_update_filters"]}
    assert updates["artwork-addition"]["count"] == 1
    assert updates["artwork-replacement"]["count"] == 0
    observations = {f["slug"]: f for f in view["artwork_filters"]}
    assert observations["artwork-missing-tracks"]["next_filter"] == (
        "artwork-missing-tracks,artwork-addition,update-structure"
    )
    view = _library_page_vars([album, other], 1, 20, filter_=view["clear_facets"]["updates"])
    assert len(view["rows"]) == 2
    view = _library_page_vars([album, other], 1, 20, filter_=query, q="Other")
    assert view["rows"] == []
    assert view["artwork_update_filter_active"]


@pytest.mark.parametrize("suffix", ["m4a", "mp3", "flac", "opus"])
def test_scan_measures_the_same_first_image_as_the_review(tmp_path, suffix):
    track = tmp_path / f"track.{suffix}"
    shutil.copy(Path(__file__).parent / f"fixtures/sine.{suffix}", track)
    formats.write_cover(track, png_bytes(63, 71))
    assert formats.read_scan_fields(track).art == formats.read_tags(track).art


def test_cached_addition_is_offline_and_not_a_tag_update(tmp_path, monkeypatch):
    album = _tagged(tmp_path / "music", _release(mbid=MBID))
    _archive(png_bytes(120, 120))
    artwork_updates.warm([album])

    def forbidden(*args, **kwargs):
        pytest.fail("Filtering read disk or used the network")

    monkeypatch.setattr(formats, "read_tags", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr(activity_store, "cached_cover_art", forbidden)
    monkeypatch.setattr(mb_lookup, "fetch_release", forbidden)
    monkeypatch.setattr(cover_art, "check_front", forbidden)
    assert artwork_updates.operation(album) is artwork.Operation.ADDITION
    assert not album.update_available
    page = _library_page_vars([album], 1, 20, filter_="artwork-addition")
    assert page["rows"] == [album]
    assert page["artwork_operations"] == {album.id: artwork.Operation.ADDITION}


def test_local_gaps_policy_and_replacement_summary_follow_the_plan(tmp_path):
    root = tmp_path / "music"
    album = _tagged(root, _release(mbid=MBID, tracks=2), tracks=2)
    paths = sorted(album.path.glob("*.m4a"))
    formats.write_cover(paths[0], png_bytes(120, 120))
    album = scanner.scan(root)[0]
    assert artwork_updates.operation(album) is artwork.Operation.ADDITION
    # Filling the other track does not require a folder-cover setting.
    formats.write_cover(paths[1], png_bytes(120, 120))
    album = scanner.scan(root)[0]
    assert artwork_updates.operation(album) is None
    artwork_updates.configure(lambda: artwork.FolderCoverPolicy.IF_MISSING)
    assert artwork_updates.operation(album) is artwork.Operation.ADDITION
    # One replacement dominates any additions, matching the review's label.
    (album.path / "cover.png").write_bytes(png_bytes(60, 60))
    formats.remove_cover(paths[1])
    album = scanner.scan(root)[0]
    assert artwork_updates.operation(album) is artwork.Operation.REPLACEMENT


def test_compilation_is_preserved_and_larger_archive_only_improves_folder(tmp_path):
    root = tmp_path / "music"
    album = _tagged(root, _release(mbid=MBID, tracks=2), tracks=2)
    paths = sorted(album.path.glob("*.m4a"))
    formats.write_cover(paths[0], png_bytes(60, 60))
    formats.write_cover(paths[1], png_bytes(61, 61))
    _archive(png_bytes(120, 120))
    artwork_updates.warm([album])
    album = scanner.scan(root)[0]
    assert artwork_updates.operation(album) is None
    formats.write_cover(paths[1], png_bytes(60, 60))
    album = scanner.scan(root)[0]
    assert artwork_updates.operation(album) is None
    (album.path / "cover.png").write_bytes(png_bytes(70, 70))
    album = scanner.scan(root)[0]
    assert artwork_updates.operation(album) is artwork.Operation.REPLACEMENT


def test_stand_in_yields_to_smaller_own_cover_but_not_group_or_rematch(tmp_path):
    root = tmp_path / "music"
    album = _tagged(root, _release(mbid=MBID))
    old = png_bytes(120, 120)
    formats.write_cover(next(album.path.glob("*.m4a")), old)
    album = scanner.scan(root)[0]
    assert album.sidecar is not None
    album.sidecar = replace(
        album.sidecar,
        borrowed_artwork=(
            BorrowedArtwork(
                formats.EmbeddedArt.of(old, "image/png").digest,
                "release-group",
                "group",
                MBID,
            ),
        ),
    )
    _archive(png_bytes(60, 60), source="release-group")
    artwork_updates.observe(MBID)
    assert artwork_updates.operation(album) is None
    _archive(png_bytes(60, 60))
    artwork_updates.observe(MBID)
    assert artwork_updates.threshold(album) == -1
    assert artwork_updates.operation(album) is artwork.Operation.REPLACEMENT
    assert album.sidecar is not None
    album.sidecar = replace(album.sidecar, mb_release_id="another-release")
    assert artwork_updates.operation(album) is None


def test_sweep_asks_once_for_copies_even_when_musicbrainz_is_fresh(tmp_path, monkeypatch):
    release = _release(mbid=MBID)
    albums = [_tagged(tmp_path / "music", release, name=name) for name in ("One", "Two")]
    activity_store.store_release(MBID, "+".join(sorted(mb_lookup.RELEASE_INCLUDES)), release)
    before = {p: p.read_bytes() for a in albums for p in a.path.iterdir()}
    calls = []

    def check(mbid, **kwargs):
        calls.append((mbid, kwargs))
        return _archive(png_bytes(90, 90))

    monkeypatch.setattr(cover_art, "check_front", check)
    monkeypatch.setattr(mb_lookup, "fetch_release", lambda *a, **kw: pytest.fail("MB request"))
    assert artwork_updates.sweep(albums, limit=20) == 1
    assert calls[0][1]["release_group_mbid"] == "rg-aaa"
    assert calls[0][1]["keep_if_wider_than"] == -1
    assert all(artwork_updates.operation(a) is artwork.Operation.ADDITION for a in albums)
    assert before == {p: p.read_bytes() for a in albums for p in a.path.iterdir()}
    assert artwork_updates.sweep(albums, limit=20) == 0
    assert len(calls) == 1
    # A restart uses durable evidence, without asking CAA or relying on an attempt timestamp.
    artwork_updates.configure(lambda: artwork.FolderCoverPolicy.NEVER)
    artwork_updates.warm(albums)
    assert artwork_updates.sweep(albums, limit=20) == 0
    assert artwork_updates.operation(albums[0]) is artwork.Operation.ADDITION
    assert len(calls) == 1


def test_negative_answer_expires_and_failure_preserves_observation(tmp_path, monkeypatch):
    album = _tagged(tmp_path / "music", _release(mbid=MBID))
    negative = activity_store.CachedCoverArt(fetched_at=datetime.now(UTC))
    activity_store.store_cover_art(MBID, negative)
    calls = []

    def check(mbid, **kwargs):
        calls.append(mbid)
        raise cover_art.CoverArtError("offline")

    monkeypatch.setattr(cover_art, "check_front", check)
    assert artwork_updates.sweep([album], limit=10) == 0
    assert calls == []
    older = replace(negative, fetched_at=datetime.now(UTC) - timedelta(days=8))
    activity_store.store_cover_art(MBID, older)
    assert artwork_updates.sweep([album], limit=10) == 0
    assert calls == [MBID]
    assert caa_cache.stored(MBID) == older
    assert artwork_updates.sweep([album], limit=10) == 0
    assert calls == [MBID]


def test_copies_use_the_smallest_folder_threshold(tmp_path, monkeypatch):
    root = tmp_path / "music"
    for name, size in (("Large", 300), ("Small", 40)):
        album = _tagged(root, _release(mbid=MBID), name=name)
        (album.path / "cover.png").write_bytes(png_bytes(size, size))
        formats.write_cover(next(album.path.glob("*.m4a")), png_bytes(250, 250))
    albums = scanner.scan(root)
    widths = []

    def check(mbid, **kwargs):
        widths.append(kwargs["keep_if_wider_than"])
        return _archive(png_bytes(100, 100))

    monkeypatch.setattr(cover_art, "check_front", check)
    assert artwork_updates.sweep(albums, limit=10) == 1
    assert widths == [40]
    assert [a.path.name for a in albums if artwork_updates.operation(a)] == ["Small"]


def test_sweep_paces_new_releases_and_stops_at_first_failure(tmp_path, monkeypatch):
    album = _tagged(tmp_path / "music", _release(mbid=MBID))
    assert album.sidecar is not None
    albums = [
        replace(album, sidecar=replace(album.sidecar, mb_release_id=f"release-{n:03}"))
        for n in range(145)
    ]
    calls = []

    def check(mbid, **kwargs):
        calls.append(mbid)
        return activity_store.CachedCoverArt(fetched_at=datetime.now(UTC))

    monkeypatch.setattr(cover_art, "check_front", check)
    assert artwork_updates.sweep(albums) == 2
    assert calls == ["release-000", "release-001"]
    assert artwork_updates.sweep(albums) == 1
    assert calls[-1] == "release-002"

    def fail(mbid, **kwargs):
        calls.append(mbid)
        raise cover_art.CoverArtError("offline")

    monkeypatch.setattr(cover_art, "check_front", fail)
    assert artwork_updates.sweep(albums, limit=20) == 0
    assert calls == ["release-000", "release-001", "release-002", "release-003"]


@pytest.fixture
def failing_archive(tmp_path, monkeypatch):
    """Exercise the worker, durable cache and real log-to-Activity mirror together."""
    album = _tagged(tmp_path / "music", _release(mbid=MBID))
    assert album.sidecar is not None
    albums = [
        replace(album, sidecar=replace(album.sidecar, mb_release_id=f"release-{n:03}"))
        for n in range(30)
    ]
    clock = [datetime.now(UTC)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]

    monkeypatch.setattr(artwork_updates, "datetime", Clock)
    # Replace this module's time reference, not the process-wide clock used by pytest.
    monkeypatch.setattr(
        artwork_updates, "time", SimpleNamespace(monotonic=lambda: clock[0].timestamp())
    )
    calls = []
    failing = [True]
    check_front = cover_art.check_front

    def check(mbid, **kwargs):
        calls.append(mbid)
        transport = httpx.MockTransport(lambda request: httpx.Response(500 if failing[0] else 404))
        with httpx.Client(transport=transport) as client:
            return check_front(mbid, client=client, **kwargs)

    monkeypatch.setattr(cover_art, "check_front", check)
    activity.install_log_handler()
    activity.clear()
    return albums, clock, calls, failing


def test_background_failure_backs_off_across_releases_and_caps_delay(failing_archive):
    albums, clock, calls, _ = failing_archive
    assert artwork_updates.sweep(albums, limit=20) == 0
    assert calls == ["release-000"]
    for delay in (10, 20, 40, 60, 60):
        clock[0] += timedelta(minutes=delay) - timedelta(seconds=1)
        before = len(calls)
        assert artwork_updates.sweep(albums, limit=20) == 0
        assert len(calls) == before
        clock[0] += timedelta(seconds=1)
        assert artwork_updates.sweep(albums, limit=20) == 0
        assert len(calls) == before + 1


def test_background_failures_and_recovery_are_logged_not_posted(failing_archive, caplog):
    albums, clock, calls, failing = failing_archive
    caplog.set_level(logging.INFO, logger="harmonist.artwork_updates")
    for _ in range(7):
        artwork_updates.sweep(albums, limit=1)
        clock[0] += timedelta(hours=1)
    assert len(calls) == 7
    # Through the real log mirror: an archive outage is not the user's to act on.
    assert activity.recent() == []
    failures = [r for r in caplog.records if r.name == "harmonist.artwork_updates"]
    assert len(failures) == 7
    assert all(r.levelno == logging.WARNING and r.exc_info for r in failures)
    assert "500" in str(failures[-1].exc_info[1])

    failing[0] = False
    assert artwork_updates.sweep(albums, limit=1) == 1
    assert activity.recent() == []
    assert "after 7 failed checks" in caplog.records[-1].getMessage()
    # Recovery resets pacing; another release can be checked immediately.
    assert artwork_updates.sweep(albums, limit=1) == 1


def test_background_failure_preserves_observation_and_configure_resets_backoff(failing_archive):
    albums, _, calls, _ = failing_archive
    previous = _archive(png_bytes(100, 100), age=timedelta(days=8))
    mbid = albums[0].sidecar.mb_release_id
    activity_store.store_cover_art(mbid, previous)
    artwork_updates.sweep(albums[:1], limit=1)
    assert caa_cache.stored(mbid) == previous
    assert artwork_updates.sweep(albums, limit=1) == 0
    assert len(calls) == 1
    artwork_updates.configure(lambda: artwork.FolderCoverPolicy.NEVER)
    artwork_updates.sweep(albums, limit=1)
    assert len(calls) == 2


def test_any_image_threshold_fetches_and_recovers_unmeasurable_artwork(tmp_path):
    requests = []
    image = b"an image whose dimensions cannot be measured"

    def respond(request):
        requests.append((str(request.url), request.headers.get("range")))
        if request.url.host == "coverartarchive.org":
            return httpx.Response(
                200, json={"images": [{"front": True, "image": "https://example.test/front.png"}]}
            )
        return httpx.Response(200, content=image, headers={"content-type": "image/png"})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        answer = cover_art.check_front(MBID, keep_if_wider_than=-1, client=client)
    activity_store.store_cover_art(MBID, answer)
    assert answer.width is None
    assert len(requests) == 3
    assert cover_art.cached_front(MBID).data == image
    assert not caa_cache.front_due(MBID, keep_if_wider_than=-1)
    cover_art.cached_image(MBID).unlink()
    assert caa_cache.front_due(MBID, keep_if_wider_than=-1)
    assert cover_art.unfetched_winner(MBID, answer, -1) == "https://example.test/front.png"


@pytest.mark.parametrize(
    ("level", "sync", "reconcile", "scanned"),
    [
        ("off", False, False, True),
        ("review", True, False, True),
        ("review", False, True, True),
        ("review", False, False, False),
    ],
)
def test_background_guards_do_not_launch_a_worker(monkeypatch, level, sync, reconcile, scanned):
    from harmonist.web import main as web_main

    monkeypatch.setattr(web_main, "_artwork_check_lock", threading.Lock())
    monkeypatch.setattr(
        threading, "Thread", lambda **kw: pytest.fail("started a guarded artwork worker")
    )
    web_main._artwork_check_if_idle(
        _stub_app(level),
        _stub_runner(running=sync),
        _stub_runner(running=reconcile),
        _stub_runner(scanned=scanned),
    )


def test_worker_warms_offline_and_has_an_independent_nonoverlapping_lock(monkeypatch):
    from harmonist.web import main as web_main

    calls = []
    monkeypatch.setattr(artwork_updates, "warm", lambda albums: calls.append("warm"))
    monkeypatch.setattr(artwork_updates, "sweep", lambda albums: calls.append("sweep"))

    class InlineThread:
        def __init__(self, *, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(threading, "Thread", InlineThread)
    args = (_stub_runner(), _stub_runner(), _stub_runner())
    web_main._artwork_check_if_idle(_stub_app("off"), *args, check=False)
    assert calls == ["warm"]
    with web_main._update_check_lock:
        web_main._artwork_check_if_idle(_stub_app(), *args)
    assert calls == ["warm", "warm", "sweep"]
    with web_main._artwork_check_lock:
        web_main._artwork_check_if_idle(_stub_app(), *args)
    assert calls == ["warm", "warm", "sweep"]
