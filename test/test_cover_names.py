"""A folder cover's name follows the image it holds (#905): `cover.jpg` for a
JPEG, `cover.png` for a PNG, so a replacement of the other type renames it —
but never over another `cover.*` already beside it."""

import io
import random

import pytest
from PIL import Image

from harmonist import (
    activity_store,
    artwork,
    artwork_store,
    cover_art,
    derived,
    images,
    tag_history,
    tagger,
)

#: What the tests squish to, as `test_artwork_squish` does.
LIMIT = 200_000


@pytest.fixture(autouse=True)
def stores(tmp_path, monkeypatch):
    activity_store.init(tmp_path / "activity.db")
    artwork_store.configure(tmp_path / "artwork")
    derived.reset()
    monkeypatch.setattr(artwork, "PLAYER_MAX_BYTES", LIMIT)
    yield
    derived.reset()


def _image(size: int, kind: str, *, seed: int) -> bytes:
    """Grain, which neither format compresses much: a 300px one is over LIMIT."""
    pixels = random.Random(seed).randbytes(size * size * 3)
    buf = io.BytesIO()
    Image.frombytes("RGB", (size, size), pixels).save(buf, kind)
    return buf.getvalue()


def _plan(album_dir, archive):
    files = sorted(album_dir.glob("*.m4a"))
    return files, tagger.decide_artwork(
        album_dir,
        files,
        cover_art.cached_cover(album_dir),
        archive=archive,
        choices={artwork.COVER: artwork.Choice.ARCHIVE},
        folder_cover=artwork.FolderCoverPolicy.IF_MISSING,
    )


def _apply(album_dir, archive, plan, files, cover_path):
    return tagger.apply_artwork(
        album_dir, plan, files=files, cover_path=cover_path, archive=archive
    )


def _audit() -> list[str]:
    conn = activity_store._ensure()
    return [r[0] for r in conn.execute("SELECT message FROM events ORDER BY id")]


def _detail():
    conn = activity_store._ensure()
    ids = [r[0] for r in conn.execute("SELECT event_id FROM tag_changes ORDER BY event_id")]
    detail = activity_store.tag_changes_for(ids)
    return [detail[i] for i in ids]


@pytest.mark.parametrize(
    ("was", "old", "new", "becomes"),
    [
        # A PNG front over a JPEG cover.
        ("cover.jpg", ("JPEG", 64), ("PNG", 100), "cover.png"),
        # A cover over the limit squished in place, which always makes a JPEG
        # (#893): *Astral Projection — Dancing Galaxy*'s shape.
        ("cover.png", ("PNG", 300), ("PNG", 300), "cover.jpg"),
    ],
    ids=["png-over-jpeg", "squished-png"],
)
def test_a_cover_replaced_by_the_other_type_is_renamed_to_match(
    album_with_tracks, was, old, new, becomes
):
    album_dir = album_with_tracks(1)
    before = _image(old[1], old[0], seed=1)
    after = before if new == old else _image(new[1], new[0], seed=2)
    (album_dir / was).write_bytes(before)
    archive = cover_art.Front(data=after, mime=f"image/{new[0].lower()}")
    files, plan = _plan(album_dir, archive)

    outcome = _apply(album_dir, archive, plan, files, album_dir / was)

    assert outcome.changed >= 1 and not (outcome.stale or outcome.failed or outcome.unkept)
    assert not (album_dir / was).exists()
    written = (album_dir / becomes).read_bytes()
    assert written[:2] == b"\xff\xd8" if becomes == "cover.jpg" else written[:4] == b"\x89PNG"
    # What it replaced is kept, as for any replacement (#470)…
    assert artwork_store.path_for(images.digest(before)) is not None
    # …and the rename has a record of its own.
    assert any(m.startswith("cover.rename") and f"was={was}" in m for m in _audit())
    # Settled: the next pass, reading the renamed cover, writes nothing.
    assert _plan(album_dir, archive)[1].changes == ()


def test_a_renamed_cover_can_be_undone(album_with_tracks):
    album_dir = album_with_tracks(1)
    before = _image(64, "JPEG", seed=1)
    (album_dir / "cover.jpg").write_bytes(before)
    archive = cover_art.Front(data=_image(100, "PNG", seed=2), mime="image/png")
    files, plan = _plan(album_dir, archive)
    _apply(album_dir, archive, plan, files, album_dir / "cover.jpg")

    restored = tagger.restore_artwork(album_dir, tag_history.artwork_revert_plan(_detail()))

    assert restored.restored >= 1 and restored.stale == ()
    cover = cover_art.cached_cover(album_dir)
    assert cover is not None and cover.read_bytes() == before


def test_a_rename_never_overwrites_the_other_cover_beside_it(album_with_tracks):
    """Both names at once: `cached_cover` reads `cover.jpg` and never shows the
    `cover.png` beside it, so renaming onto it would destroy a file the user
    was never shown. The cover is written in place instead, as it always was."""
    album_dir = album_with_tracks(1)
    (album_dir / "cover.jpg").write_bytes(_image(64, "JPEG", seed=1))
    hidden = _image(32, "PNG", seed=3)
    (album_dir / "cover.png").write_bytes(hidden)
    new = _image(100, "PNG", seed=2)
    archive = cover_art.Front(data=new, mime="image/png")
    files, plan = _plan(album_dir, archive)

    outcome = _apply(album_dir, archive, plan, files, album_dir / "cover.jpg")

    assert not (outcome.stale or outcome.failed or outcome.unkept)
    assert (album_dir / "cover.png").read_bytes() == hidden
    assert (album_dir / "cover.jpg").read_bytes() == new
    assert not any(m.startswith("cover.rename") for m in _audit())


def test_a_cover_that_appears_where_a_rename_lands_makes_it_stale(album_with_tracks):
    album_dir = album_with_tracks(1)
    before = _image(64, "JPEG", seed=1)
    (album_dir / "cover.jpg").write_bytes(before)
    archive = cover_art.Front(data=_image(100, "PNG", seed=2), mime="image/png")
    files, plan = _plan(album_dir, archive)
    theirs = _image(32, "PNG", seed=3)
    (album_dir / "cover.png").write_bytes(theirs)  # after the plan was made

    outcome = _apply(album_dir, archive, plan, files, album_dir / "cover.jpg")

    assert outcome.stale == ("cover.jpg",)
    assert (album_dir / "cover.jpg").read_bytes() == before
    assert (album_dir / "cover.png").read_bytes() == theirs
