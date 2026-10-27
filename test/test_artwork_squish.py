"""Squishing an image over the size limit as it's written, and counting the
copy as its original afterwards (#888)."""

import io
import logging
import random
from pathlib import Path

import pytest
from PIL import Image

from harmonist import (
    activity_store,
    artwork,
    artwork_store,
    cover_art,
    derived,
    formats,
    images,
    tag_history,
    tagger,
)

#: What the tests squish to: a quarter-megabyte of grain at quality 100 fits
#: at quality 95, as the Mogwai cover does at its own scale.
LIMIT = 200_000


@pytest.fixture(autouse=True)
def stores(tmp_path, monkeypatch):
    activity_store.init(tmp_path / "activity.db")
    artwork_store.configure(tmp_path / "artwork")
    derived.reset()
    monkeypatch.setattr(artwork, "PLAYER_MAX_BYTES", LIMIT)
    yield
    derived.reset()


def _jpeg(size: int, *, seed: int, quality: int = 100) -> bytes:
    pixels = random.Random(seed).randbytes(size * size * 3)
    buf = io.BytesIO()
    Image.frombytes("RGB", (size, size), pixels).save(buf, "JPEG", quality=quality, subsampling=0)
    return buf.getvalue()


def _art(track) -> bytes:
    """The image a track carries; it has one."""
    found = formats.read_cover(track)
    assert found is not None
    return found[0]


def _detail():
    conn = activity_store._ensure()
    ids = [r[0] for r in conn.execute("SELECT event_id FROM tag_changes ORDER BY event_id")]
    detail = activity_store.tag_changes_for(ids)
    return [detail[i] for i in ids]


def _apply_archive(album_dir, small, big):
    files = sorted(album_dir.glob("*.m4a"))
    archive = cover_art.Front(data=big, mime="image/jpeg")
    plan = tagger.decide_artwork(
        album_dir,
        files,
        None,
        archive=archive,
        choices={images.digest(small): artwork.Choice.ARCHIVE},
        folder_cover=artwork.FolderCoverPolicy.IF_MISSING,
    )
    return files, tagger.apply_artwork(
        album_dir, plan, files=files, cover_path=None, archive=archive
    )


def test_an_image_over_the_limit_is_written_squished_everywhere(album_with_tracks):
    album_dir = album_with_tracks(2)
    small = _jpeg(64, seed=1, quality=75)
    big = _jpeg(256, seed=2)
    assert len(big) > LIMIT
    for track in album_dir.glob("*.m4a"):
        formats.write_cover(track, small)

    files, outcome = _apply_archive(album_dir, small, big)

    assert outcome.changed == 3 and not (outcome.stale or outcome.failed)
    written = {_art(f) for f in files} | {(album_dir / "cover.jpg").read_bytes()}
    assert len(written) == 1
    squished = written.pop()
    assert len(squished) <= LIMIT
    assert images.dimensions(squished) == images.Size(256, 256)
    # The copy answers as the image that was picked, durably.
    assert derived.originals() == {images.digest(squished): images.digest(big)}
    derived.reset()
    assert derived.originals() == {images.digest(squished): images.digest(big)}
    # The outcome reports the copy, as the album carries it: what a stand-in
    # record names (#663).
    assert outcome.written == (images.digest(squished),)


def test_undo_puts_back_what_a_squished_write_replaced(album_with_tracks):
    """The history records the copy actually written, which is what Undo
    checks a file still holds before restoring it."""
    album_dir = album_with_tracks(2)
    small = _jpeg(64, seed=1, quality=75)
    big = _jpeg(256, seed=2)
    for track in album_dir.glob("*.m4a"):
        formats.write_cover(track, small)
    files, _ = _apply_archive(album_dir, small, big)

    restored = tagger.restore_artwork(album_dir, tag_history.artwork_revert_plan(_detail()))

    assert restored.stale == ()
    assert {_art(f) for f in files} == {small}
    assert not (album_dir / "cover.jpg").exists()


@pytest.mark.parametrize("mapped", [True, False], ids=["mapped", "unmapped"])
def test_nothing_is_left_to_apply_after_a_squished_write(tmp_path, monkeypatch, mapped):
    """A tagging and the Library both plan again from the files. A copy shrunk
    to fit is smaller than its original, so unless they read the mapping, the
    original is offered over its own copy on every pass."""
    from datetime import UTC, datetime, timedelta

    from harmonist import artwork_updates, caa_cache, scanner
    from test.test_gardener import _release, _tagged

    monkeypatch.setattr(artwork, "PLAYER_MAX_BYTES", 8_000)  # so the copy shrinks
    cover_art.configure_cache(tmp_path / "caa")
    caa_cache.configure(timedelta(days=7))
    artwork_updates.configure(lambda: artwork.FolderCoverPolicy.IF_MISSING)
    mbid = "rel-squish"
    album = _tagged(tmp_path / "music", _release(tracks=2, mbid=mbid), tracks=2)
    big = _jpeg(256, seed=2)
    activity_store.store_cover_art(
        mbid,
        activity_store.CachedCoverArt(
            fetched_at=datetime.now(UTC),
            image_url="https://example.test/front.jpg",
            width=256,
            height=256,
            length=len(big),
            mime="image/jpeg",
            source="release",
        ),
    )
    cover_art.cache_image(mbid, big, "image/jpeg")
    files = sorted(album.path.glob("*.m4a"))
    archive = cover_art.Front(data=big, mime="image/jpeg")
    plan = tagger.decide_artwork(album.path, files, None, archive=archive)
    tagger.apply_artwork(album.path, plan, files=files, cover_path=None, archive=archive)
    cover = album.path / "cover.jpg"
    size = images.dimensions(cover.read_bytes())
    assert size is not None and size.width < 256
    if not mapped:
        activity_store.clear()
        derived.reset()

    again = tagger.decide_artwork(album.path, files, cover, archive=archive)
    rescanned = scanner.scan(tmp_path / "music")[0]
    artwork_updates.warm([rescanned])
    try:
        assert bool(again.changes) is not mapped
        assert (artwork_updates.operation(rescanned) is None) is mapped
    finally:
        artwork_updates.configure(lambda: artwork.FolderCoverPolicy.NEVER)
        cover_art.configure_cache(None)


def test_an_image_it_cannot_decode_is_written_as_it_is(album_with_tracks, caplog):
    """Not squishing loses nothing the album had; refusing the write would
    cost the user the image they picked."""
    album_dir = album_with_tracks(1)
    small = _jpeg(64, seed=1, quality=75)
    track = next(album_dir.glob("*.m4a"))
    formats.write_cover(track, small)
    undecodable = b"\xff\xd8\xff\xe0" + bytes(LIMIT)

    with caplog.at_level(logging.WARNING):
        _, outcome = _apply_archive(album_dir, small, undecodable)

    assert outcome.changed >= 1
    assert _art(track) == undecodable
    assert "could not squish" in caplog.text
    assert derived.originals() == {}


# ---------------------------------------------------------------------------
# The readers: a copy counts as its original wherever the album is asked
# whether it carries an image. Each runs without the mapping too, which is the
# gap it closes.
# ---------------------------------------------------------------------------

ORIGINAL = formats.EmbeddedArt(
    digest="0" * 64, mime="image/jpeg", length=30_000_000, size=images.Size(4000, 4000)
)
#: Squished at its own dimensions, as the first steps do.
COPY = formats.EmbeddedArt(
    digest="1" * 64, mime="image/jpeg", length=16_000_000, size=images.Size(4000, 4000)
)
#: Squished smaller, as the last resort does: now the original "beats" it.
SHRUNK = formats.EmbeddedArt(
    digest="2" * 64, mime="image/jpeg", length=19_000_000, size=images.Size(3200, 3200)
)
ORIGINALS = {COPY.digest: ORIGINAL.digest, SHRUNK.digest: ORIGINAL.digest}
BOTH_WAYS = pytest.mark.parametrize("originals", [ORIGINALS, None], ids=["mapped", "unmapped"])


@BOTH_WAYS
def test_a_copy_of_a_replaced_front_is_that_front(originals):
    """#886: the archive replaced its front, and the album carries a copy of the old one."""
    replaced = activity_store.ReplacedFront(
        image_url="https://example.test/old.jpg", digest=ORIGINAL.digest
    )
    found = artwork.superseded(replaced, [COPY], originals=originals)
    assert found == ({COPY.digest} if originals else set())


@BOTH_WAYS
def test_choosing_the_image_a_copy_was_made_from_writes_nothing(tmp_path, originals):
    tracks = [(tmp_path / "01.m4a", COPY), (tmp_path / "02.m4a", COPY)]
    plan = artwork.plan(
        tmp_path,
        tracks,
        artwork.FolderCover(name="cover.jpg", image=COPY, path=tmp_path / "cover.jpg"),
        choices={COPY.digest: "777", artwork.COVER: "777"},
        picks={"777": ORIGINAL},
        originals=originals,
    )
    assert bool(plan.changes) is (originals is None)


@BOTH_WAYS
@pytest.mark.parametrize("carrier", ["tracks", "cover"])
def test_the_original_never_beats_its_own_shrunk_copy(tmp_path, originals, carrier):
    """Else a copy shrunk to fit is offered its original again on every pass,
    and squished back to itself on every apply."""
    tracks: list[tuple[Path, formats.EmbeddedArt | None]]
    if carrier == "tracks":
        tracks = [(tmp_path / "01.m4a", SHRUNK)]
        cover = None
    else:
        tracks = [(tmp_path / "01.m4a", None)]
        cover = artwork.FolderCover(name="cover.jpg", image=SHRUNK, path=tmp_path / "cover.jpg")
    plan = artwork.plan(
        tmp_path,
        tracks,
        cover,
        ORIGINAL,
        folder_cover=artwork.FolderCoverPolicy.NEVER,
        lossless=True,
        originals=originals,
    )
    # A track with no art is still filled; only the carriers holding the copy
    # are in question.
    rewrites = [c for c in plan.changes if c.before == SHRUNK.digest]
    assert bool(rewrites) is (originals is None)


@BOTH_WAYS
def test_the_section_reads_a_copy_as_the_image_it_was_made_from(tmp_path, originals):
    candidate = artwork.Candidate(
        image_id="777",
        origin=artwork.Candidate.RELEASE,
        front=True,
        types=("front",),
        image=ORIGINAL,
    )
    view = artwork.summarise(
        tmp_path,
        [(tmp_path / "01.m4a", formats.TrackTags(art=COPY))],
        None,
        None,
        ORIGINAL,
        candidates=[candidate],
        folder_cover=artwork.FolderCoverPolicy.NEVER,
        originals=originals,
    )
    row = view.rows[0]
    assert view.unchanged(row, candidate) is (originals is not None)
    assert row.has_archive is (originals is not None)


@BOTH_WAYS
def test_a_watch_proposes_nothing_the_album_carries_a_copy_of(tmp_path, monkeypatch, originals):
    from harmonist import artwork_watch
    from harmonist.models import ArtworkWatch, Sidecar

    if originals:
        derived.record(COPY.digest, original=ORIGINAL.digest)
    listed = cover_art.Candidate(
        "777", "https://example.test/777.jpg", "https://example.test/777-250.jpg", ("front",), True
    )
    monkeypatch.setitem(artwork_watch._observed, "rel", (artwork_watch.Image(listed, ORIGINAL),))
    sc = Sidecar(mb_release_id="rel", artwork_watch=ArtworkWatch(release="rel"))

    choices, _ = artwork_watch.proposal(
        sc, [(tmp_path / "01.m4a", COPY)], None, artwork.FolderCoverPolicy.NEVER
    )

    assert bool(choices) is (originals is None)


@pytest.mark.parametrize("mapped", [True, False], ids=["mapped", "unmapped"])
def test_a_squished_stand_in_that_was_the_releases_own_front_settles(
    album_with_tracks, monkeypatch, mapped
):
    """#663: the group's front is often one release's own. Once the release's
    own front turns out to be the very image borrowed, the record goes, even
    when what the album carries is a copy squished from it."""
    from harmonist import scanner
    from harmonist import sidecar as sidecar_mod
    from harmonist.models import BorrowedArtwork, Sidecar
    from harmonist.web import main

    album_dir = album_with_tracks(1)
    big = _jpeg(256, seed=2)
    copy = "c" * 64
    if mapped:
        derived.record(copy, original=images.digest(big))
    borrowed = BorrowedArtwork(digest=copy, source="release-group", source_mbid="rg", release="rel")
    sidecar_mod.write(album_dir, Sidecar(mb_release_id="rel", borrowed_artwork=(borrowed,)))
    monkeypatch.setattr(
        cover_art,
        "cached_front",
        lambda mbid, release_only=False: cover_art.Front(data=big, mime="image/jpeg"),
    )
    album = scanner.scan(album_dir.parent.parent)[0]

    assert main._settle_own_front(album, "rel") is mapped
    assert sidecar_mod.read(album_dir).borrowed_artwork == (() if mapped else (borrowed,))


@BOTH_WAYS
def test_a_borrowed_image_written_squished_is_still_a_stand_in(tmp_path, monkeypatch, originals):
    """#663: the record names the copy, as the album carries it, and is found
    from the archive image it was squished from."""
    from dataclasses import replace

    from harmonist import caa_listings
    from harmonist.models import BorrowedArtwork

    if originals:
        derived.record(COPY.digest, original=ORIGINAL.digest)
    borrowed = BorrowedArtwork(
        digest=ORIGINAL.digest, source="release-group", source_mbid="rg", release="rel"
    )
    monkeypatch.setattr(caa_listings, "_has_own_front", lambda mbid: False)
    monkeypatch.setattr(caa_listings, "_borrowable", lambda mbid: {ORIGINAL.digest: borrowed})

    kept = caa_listings.stand_ins_after(tmp_path, [], "rel", (), [COPY.digest])

    assert kept == ((replace(borrowed, digest=COPY.digest),) if originals else ())


# ---------------------------------------------------------------------------
# An image the album already has over the limit is offered squished (#893).
# Holding the wanted image isn't enough when it's the original over the limit:
# writing it would change it.
# ---------------------------------------------------------------------------

#: Exactly at the limit, which isn't over it.
FITS = formats.EmbeddedArt(
    digest="3" * 64, mime="image/jpeg", length=LIMIT, size=images.Size(1000, 1000)
)


def _carrying(tmp_path, carrier, image):
    """Tracks and a folder cover with `image` on `carrier` alone."""
    if carrier == "tracks":
        return [(tmp_path / "01.m4a", image), (tmp_path / "02.m4a", image)], None
    cover = artwork.FolderCover(name="cover.jpg", image=image, path=tmp_path / "cover.jpg")
    return [(tmp_path / "01.m4a", None)], cover


@pytest.mark.parametrize("carrier", ["tracks", "cover"])
@pytest.mark.parametrize(
    ("image", "offered"), [(ORIGINAL, True), (FITS, False)], ids=["over", "at"]
)
def test_an_image_the_album_has_over_the_limit_is_offered_squished(
    tmp_path, carrier, image, offered
):
    """The size rule keeps what the album has, and keeping an image over the
    limit means writing it again, squished: a Replacement of each carrier with
    the image it already holds."""
    tracks, cover = _carrying(tmp_path, carrier, image)

    plan = artwork.plan(tmp_path, tracks, cover, folder_cover=artwork.FolderCoverPolicy.NEVER)

    rewrites = [(c.after, c.operation) for c in plan.changes if c.before == image.digest]
    expected = [(image.digest, artwork.Operation.REPLACEMENT)] * (2 if carrier == "tracks" else 1)
    assert rewrites == (expected if offered else [])


@pytest.mark.parametrize("carrier", ["tracks", "cover"])
def test_choosing_the_image_a_carrier_holds_over_the_limit_writes_it(tmp_path, carrier):
    """Using the archive's image a carrier already holds, byte for byte, is
    no longer a no-op when that image is over the limit: The White Room's
    Use, which said Unchanged once the original was in hand."""
    tracks, cover = _carrying(tmp_path, carrier, ORIGINAL)
    choose = artwork.COVER if carrier == "cover" else ORIGINAL.digest

    plan = artwork.plan(
        tmp_path,
        tracks,
        cover,
        choices={choose: "777"},
        picks={"777": ORIGINAL},
        folder_cover=artwork.FolderCoverPolicy.NEVER,
    )

    assert [c.after for c in plan.changes if c.before == ORIGINAL.digest] == [ORIGINAL.digest] * (
        2 if carrier == "tracks" else 1
    )


@pytest.mark.parametrize("carrier", ["tracks", "cover"])
def test_keeping_an_image_over_the_limit_leaves_it_as_it_is(tmp_path, carrier):
    """The way out: the row's Keep drops the squish, as it drops any change."""
    tracks, cover = _carrying(tmp_path, carrier, ORIGINAL)
    keep = artwork.COVER if carrier == "cover" else ORIGINAL.digest

    plan = artwork.plan(
        tmp_path,
        tracks,
        cover,
        choices={keep: artwork.Choice.KEEP},
        folder_cover=artwork.FolderCoverPolicy.NEVER,
    )

    assert not [c for c in plan.changes if c.before == ORIGINAL.digest]


def test_a_compilations_folder_cover_over_the_limit_is_offered_squished(tmp_path):
    """Per-track sleeves are left alone, as ever; the folder cover is a
    carrier of its own."""
    cover = artwork.FolderCover(name="cover.jpg", image=ORIGINAL, path=tmp_path / "cover.jpg")
    tracks = [(tmp_path / "01.m4a", FITS), (tmp_path / "02.m4a", COPY)]

    plan = artwork.plan(tmp_path, tracks, cover, folder_cover=artwork.FolderCoverPolicy.NEVER)

    assert [(c.target.name, c.before, c.after) for c in plan.changes] == [
        ("cover.jpg", ORIGINAL.digest, ORIGINAL.digest)
    ]
    assert plan.preserves_per_track_art


def test_the_picker_offers_use_on_a_row_keeping_its_image_over_the_limit(tmp_path):
    """Use is no longer Unchanged on a row that keeps an image over the limit,
    since using it would squish it. Without the Keep the row is already
    getting it, so it is."""
    candidate = artwork.Candidate(
        image_id="777",
        origin=artwork.Candidate.RELEASE,
        front=True,
        types=("front",),
        image=ORIGINAL,
    )
    tracks = [(tmp_path / "01.m4a", formats.TrackTags(art=ORIGINAL))]

    def view(choices):
        return artwork.summarise(
            tmp_path,
            tracks,
            None,
            None,
            ORIGINAL,
            choices=choices,
            picks={"777": ORIGINAL},
            candidates=[candidate],
            folder_cover=artwork.FolderCoverPolicy.NEVER,
        )

    suggested = view(None)
    kept = view({ORIGINAL.digest: artwork.Choice.KEEP})

    assert suggested.unchanged(suggested.rows[0], candidate)
    assert not kept.unchanged(kept.rows[0], candidate)


def test_an_oversize_cover_the_album_has_is_squished_and_then_settles(album_with_tracks):
    """Applied, it's written squished at the same size; planned again, the
    copy holds its original and nothing is left; undone, the original is
    back byte for byte."""
    album_dir = album_with_tracks(2)
    small = _jpeg(64, seed=1, quality=75)
    big = _jpeg(256, seed=2)
    files = sorted(album_dir.glob("*.m4a"))
    for track in files:
        formats.write_cover(track, small)
    cover = album_dir / "cover.jpg"
    cover.write_bytes(big)

    plan = tagger.decide_artwork(album_dir, files, cover)
    assert [(c.target, c.before, c.after) for c in plan.changes] == [
        (cover, images.digest(big), images.digest(big))
    ]
    outcome = tagger.apply_artwork(album_dir, plan, files=files, cover_path=cover)

    squished = cover.read_bytes()
    assert outcome.changed == 1 and len(squished) <= LIMIT
    assert images.dimensions(squished) == images.Size(256, 256)
    assert {_art(f) for f in files} == {small}
    assert tagger.decide_artwork(album_dir, files, cover).changes == ()

    restored = tagger.restore_artwork(album_dir, tag_history.artwork_revert_plan(_detail()))

    assert restored.stale == () and cover.read_bytes() == big


def test_the_library_finds_an_oversize_image_to_squish_as_a_replacement(tmp_path):
    """Found by the Library's own plan, from the scan alone, and routed as a
    Replacement like any other; gone once it's applied."""
    from harmonist import artwork_updates, scanner
    from test.test_gardener import _release, _tagged

    album = _tagged(tmp_path / "music", _release(tracks=1), tracks=1)
    cover = album.path / "cover.jpg"
    cover.write_bytes(_jpeg(256, seed=2))
    scanned = scanner.scan(tmp_path / "music")[0]

    assert artwork_updates.operation(scanned) is artwork.Operation.REPLACEMENT

    files = sorted(album.path.glob("*.m4a"))
    plan = tagger.decide_artwork(album.path, files, cover)
    tagger.apply_artwork(album.path, plan, files=files, cover_path=cover)

    assert artwork_updates.operation(scanner.scan(tmp_path / "music")[0]) is None
