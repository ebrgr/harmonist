"""Squishing an image over the size limit as it's written, and counting the
copy as its original afterwards (#888)."""

import io
import logging
import random

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
    written = {formats.read_cover(f)[0] for f in files} | {(album_dir / "cover.jpg").read_bytes()}
    assert len(written) == 1
    squished = written.pop()
    assert len(squished) <= LIMIT
    assert images.dimensions(squished) == images.Size(256, 256)
    # The copy answers as the image that was picked, durably.
    assert derived.originals() == {images.digest(squished): images.digest(big)}
    derived.reset()
    assert derived.originals() == {images.digest(squished): images.digest(big)}
    # What was picked is what the outcome reports, so a stand-in is recorded
    # against the archive's image, not the copy (#663).
    assert outcome.written == (images.digest(big),)


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
    assert {formats.read_cover(f)[0] for f in files} == {small}
    assert not (album_dir / "cover.jpg").exists()


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
    assert formats.read_cover(track)[0] == undecodable
    assert "could not squish" in caplog.text
    assert derived.originals() == {}
