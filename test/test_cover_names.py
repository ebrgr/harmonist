"""A folder cover's name follows the image it holds (#905): `cover.jpg` for a
JPEG, `cover.png` for a PNG, so a replacement of the other type renames it —
but never over another `cover.*` already beside it."""

import io
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
    id_registry,
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


def test_a_cover_made_from_a_mislabelled_embedded_image_is_named_for_its_bytes(
    album_with_tracks,
):
    """Another tagger can label an embedded PNG a JPEG, and the name used to
    follow the label: a `cover.jpg` holding a PNG."""
    from mutagen.mp4 import MP4, MP4Cover

    album_dir = album_with_tracks(1)
    png = _image(100, "PNG", seed=1)
    track = next(album_dir.glob("*.m4a"))
    audio = MP4(track)
    audio["covr"] = [MP4Cover(png, imageformat=MP4Cover.FORMAT_JPEG)]
    audio.save()
    files = [track]
    plan = tagger.decide_artwork(
        album_dir, files, None, folder_cover=artwork.FolderCoverPolicy.IF_MISSING
    )
    cover = plan.cover_change(artwork.Scope.ALL)
    assert cover is not None and cover.target == album_dir / "cover.png"

    tagger.apply_artwork(album_dir, plan, files=files, cover_path=None)

    assert (album_dir / "cover.png").read_bytes() == png
    assert not (album_dir / "cover.jpg").exists()


def test_a_renamed_cover_can_be_undone(album_with_tracks):
    album_dir = album_with_tracks(1)
    before = _image(64, "JPEG", seed=1)
    (album_dir / "cover.jpg").write_bytes(before)
    archive = cover_art.Front(data=_image(100, "PNG", seed=2), mime="image/png")
    files, plan = _plan(album_dir, archive)
    _apply(album_dir, archive, plan, files, album_dir / "cover.jpg")

    restored = tagger.restore_artwork(album_dir, tag_history.artwork_revert_plan(_detail()))

    assert restored.restored >= 1 and restored.stale == ()
    # Under its old name, not the one the replacement gave it (#907).
    assert (album_dir / "cover.jpg").read_bytes() == before
    assert not (album_dir / "cover.png").exists()


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


# ---------------------------------------------------------------------------
# A cover already named for the other type is offered the rename on its own
# (#907): *Björk — Volta*'s `cover.jpg` holding a PNG.
# ---------------------------------------------------------------------------


def _misnamed(album_with_tracks, was: str, kind: str) -> tuple[Path, bytes]:
    """An album whose track carries the image its folder cover holds, under
    the other type's name: the rename is all there is to do."""
    album_dir = album_with_tracks(1)
    data = _image(64, kind, seed=1)
    formats.write_cover(next(album_dir.glob("*.m4a")), data)
    (album_dir / was).write_bytes(data)
    return album_dir, data


def _suggested(album_dir, **kw):
    files = sorted(album_dir.glob("*.m4a"))
    return files, tagger.decide_artwork(album_dir, files, cover_art.cached_cover(album_dir), **kw)


@pytest.mark.parametrize(
    ("was", "kind", "becomes"),
    [("cover.jpg", "PNG", "cover.png"), ("cover.png", "JPEG", "cover.jpg")],
    ids=["png-as-jpg", "jpeg-as-png"],
)
def test_a_cover_named_for_the_other_type_is_renamed_as_it_is(
    album_with_tracks, was, kind, becomes
):
    album_dir, data = _misnamed(album_with_tracks, was, kind)
    inode = (album_dir / was).stat().st_ino
    files, plan = _suggested(album_dir)

    (change,) = plan.changes
    assert (change.target.name, change.rename_to and change.rename_to.name) == (was, becomes)
    # An Addition: it loses nothing, so a tagging and auto-apply may make it.
    assert plan.operation(artwork.Scope.ALL) is artwork.Operation.ADDITION
    assert plan.scoped(artwork.Scope.ADDITIONS) == (change,)

    outcome = tagger.apply_artwork(album_dir, plan, files=files, cover_path=album_dir / was)

    assert (outcome.changed, outcome.written) == (1, ())
    assert not (album_dir / was).exists()
    # Moved, not rewritten: the same file under its new name.
    assert (album_dir / becomes).read_bytes() == data
    assert (album_dir / becomes).stat().st_ino == inode
    messages = _audit()
    assert any(m.startswith("cover.rename") and f"was={was}" in m for m in messages)
    assert not any(m.startswith("cover.write") for m in messages)
    # Nothing was destroyed, so nothing was kept, and no backup is claimed.
    assert artwork_store.path_for(images.digest(data)) is None
    assert activity_store.artwork_backups() == []
    assert _suggested(album_dir)[1].changes == ()


def test_a_rename_is_not_offered_onto_the_other_cover_beside_it(album_with_tracks):
    album_dir, _ = _misnamed(album_with_tracks, "cover.jpg", "PNG")
    (album_dir / "cover.png").write_bytes(_image(32, "PNG", seed=3))

    assert _suggested(album_dir)[1].changes == ()


def test_keeping_the_cover_as_it_is_drops_its_rename(album_with_tracks):
    album_dir, _ = _misnamed(album_with_tracks, "cover.jpg", "PNG")

    _, plan = _suggested(album_dir, choices={artwork.COVER: artwork.Choice.KEEP})

    assert plan.changes == ()


def test_a_rename_on_its_own_is_undone_to_the_old_name(album_with_tracks):
    album_dir, data = _misnamed(album_with_tracks, "cover.jpg", "PNG")
    files, plan = _suggested(album_dir)
    # No cover path, as a tagging may pass: a rename reads no image to write.
    tagger.apply_artwork(album_dir, plan, files=files, cover_path=None)
    revert = tag_history.artwork_revert_plan(_detail())
    assert [(r.file, r.renamed_from) for r in revert] == [("cover.png", "cover.jpg")]

    restored = tagger.restore_artwork(album_dir, revert)
    again = tagger.restore_artwork(album_dir, revert)

    assert (restored.restored, restored.stale) == (1, ())
    assert (again.changed, again.stale) == (0, ())  # already back
    assert (album_dir / "cover.jpg").read_bytes() == data
    assert not (album_dir / "cover.png").exists()


def test_undoing_a_rename_leaves_a_cover_that_took_the_old_name_since(album_with_tracks):
    album_dir, data = _misnamed(album_with_tracks, "cover.jpg", "PNG")
    files, plan = _suggested(album_dir)
    tagger.apply_artwork(album_dir, plan, files=files, cover_path=album_dir / "cover.jpg")
    theirs = _image(32, "JPEG", seed=3)
    (album_dir / "cover.jpg").write_bytes(theirs)

    restored = tagger.restore_artwork(album_dir, tag_history.artwork_revert_plan(_detail()))

    assert (restored.changed, restored.stale) == (0, ("cover.png",))
    assert (album_dir / "cover.jpg").read_bytes() == theirs
    assert (album_dir / "cover.png").read_bytes() == data


def test_history_shows_a_rename_as_the_file_name_alone(album_with_tracks):
    album_dir, _ = _misnamed(album_with_tracks, "cover.jpg", "PNG")
    files, plan = _suggested(album_dir)
    tagger.apply_artwork(album_dir, plan, files=files, cover_path=album_dir / "cover.jpg")

    rows = tag_history.summarise(_detail())

    assert [(r.label, r.before, r.after) for r in rows] == [("File name", "cover.jpg", "cover.png")]
    # …and no tag Undo would try to revert it as a tag.
    assert tag_history.revert_plan(_detail()) == ()


def test_a_rescan_sees_a_renamed_cover(album_with_tracks):
    """A rename keeps the file's mtime, so a scan cache keyed on the cover's
    mtime alone served the old path: the album page then said the renamed
    cover wasn't in the folder."""
    from harmonist import scanner

    album_dir, _ = _misnamed(album_with_tracks, "cover.jpg", "PNG")
    cache: scanner.AlbumCache = {}
    (first,) = scanner.scan(album_dir.parent.parent, album_cache=cache)
    assert first.cover_path == album_dir / "cover.jpg"

    (album_dir / "cover.jpg").replace(album_dir / "cover.png")
    (again,) = scanner.scan(album_dir.parent.parent, album_cache=cache)

    assert again.cover_path == album_dir / "cover.png"


def test_history_offers_undo_for_a_rename_that_kept_nothing(album_with_tracks):
    """Undo is offered only where every image it would put back is kept. A
    rename puts back only a name, so it needs none."""
    from harmonist.web import main

    album_dir, _ = _misnamed(album_with_tracks, "cover.jpg", "PNG")
    files, plan = _suggested(album_dir)
    tagger.apply_artwork(album_dir, plan, files=files, cover_path=album_dir / "cover.jpg")
    history = activity_store.album_history(id_registry.peek(album_dir))
    detail = activity_store.tag_changes_for([e.id for e in history])

    assert main._restorable_anchors(history, detail)
