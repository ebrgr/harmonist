"""Applying the tag updates nobody has to review (#273).

The user chooses the highest significance Harmonist may write by itself
(`GardenerConfig.apply_up_to`). This pass writes every waiting update in that
range, and holds back, with a reason, anything that cannot be written with
nobody watching. A held update waits in the Inbox, which is where whatever
auto-tagging could not handle is meant to turn up.

**Written from the stored release.** The verdict an album is judged by comes
from the release in `mb_cache`, so that is what is written, as a reviewed
confirmation writes what its review showed (#532): no MusicBrainz request, and
no chance of writing a newer release than the one judged. Each album is
re-judged against it at write time, because a flag can be older than the
payload beside it.

**Tags only.** The write passes `artwork_included=False`, so the artwork store's
*no backup, no overwrite* rule has nothing to govern here.

**Held updates live in memory**, like the flags they qualify, keyed to the
MusicBrainz version they were held at. An album is retried when MusicBrainz
moves, or after a restart, rather than on every pass: a failure that recurs
every ten minutes is one log line repeated, not news.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from . import activity, activity_store, gardener, library_titles, mb_cache, progress, retag
from . import config as config_mod
from . import sidecar as sidecar_mod
from . import tagger as tagger_mod
from .formats import owned
from .models import Album, AlbumState

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Held:
    version: str | None
    reason: str


@dataclass(frozen=True)
class PassResult:
    applied: int
    held: int


_held: dict[str, _Held] = {}
_held_lock = threading.Lock()
# One pass at a time: the tick, a Settings save and the Settings re-check can
# all start one, and two over the same albums would write each one twice.
_pass_lock = threading.Lock()


def held_reason(album: Album) -> str | None:
    """Why this album's update was not applied, while that still holds."""
    with _held_lock:
        entry = _held.get(album.id)
    return entry.reason if entry is not None and entry.version == album.mb_version else None


def is_held(album: Album) -> bool:
    return held_reason(album) is not None


def reset() -> None:
    """Forget every held update, for tests."""
    with _held_lock:
        _held.clear()


def _hold(album: Album, reason: str) -> None:
    with _held_lock:
        _held[album.id] = _Held(album.mb_version, reason)


def _release_hold(album: Album) -> None:
    with _held_lock:
        _held.pop(album.id, None)


def due(
    albums: Sequence[Album],
    ignored: Mapping[str, activity_store.IgnoredUpdate],
    apply_up_to: config_mod.ReviewFrom,
) -> list[Album]:
    """Library albums with an update in the apply range, not ignored, not held."""
    return [
        a
        for a in albums
        if (level := gardener.waiting_at(a, ignored)) is not None
        and gardener.applies(level, apply_up_to)
        and not is_held(a)
    ]


def apply_due(
    albums: Sequence[Album],
    cfg: config_mod.Config,
    tagger: tagger_mod.Tagger,
    ignored: Mapping[str, activity_store.IgnoredUpdate],
    *,
    on_processed: Callable[[Album], None] | None = None,
) -> PassResult:
    """Write every due update, shown in the status bar while it runs (#757).

    A pass already running is left to finish, and this returns having done
    nothing: it is working through the same albums.

    Blocking on the disk, so call it from a worker thread, never the event loop.
    `on_processed` refreshes the snapshot after each attempt, even a failed one
    that may have written some of the files.
    """
    if not _pass_lock.acquire(blocking=False):
        return PassResult(applied=0, held=0)
    try:
        todo = due(albums, ignored, cfg.gardener.apply_up_to)
        if not todo:
            return PassResult(applied=0, held=0)
        applied = held = 0
        with progress.job("Applying updates", total=len(todo)) as job:
            # Named, so the bar says which album its tracks belong to (#815).
            for album in job.items(todo, lambda a: a.label):
                try:
                    if _apply_one(album, cfg, tagger):
                        applied += 1
                    elif is_held(album):
                        held += 1
                finally:
                    if on_processed is not None:
                        on_processed(album)
            job.closing = _summary(applied, held)
        log.info("auto-apply: %d album(s) updated, %d held for review", applied, held)
        return PassResult(applied=applied, held=held)
    finally:
        _pass_lock.release()


def _summary(applied: int, held: int) -> str:
    done = f"{applied} album updated" if applied == 1 else f"{applied} albums updated"
    return f"{done}, {held} held for review" if held else done


def _apply_one(album: Album, cfg: config_mod.Config, tagger: tagger_mod.Tagger) -> bool:
    """Write one album's update, or hold it with a reason. True when written."""
    sc = album.sidecar
    mbid = sc.mb_release_id if sc else None
    release = mb_cache.stored_release(mbid) if mbid else None
    if mbid is None or release is None:
        # The flag was judged against a stored release; without one there is
        # nothing to write from, and fetching one is not this pass's to spend.
        _hold(album, "Its MusicBrainz release isn't stored, so it is applied from its page")
        return False
    gardener.refresh_flag(album, release)
    if not album.update_available or album.update_significance is None:
        _release_hold(album)
        return False  # the files caught up on their own; nothing to write
    level = owned.Significance(album.update_significance)
    if not gardener.applies(level, cfg.gardener.apply_up_to):
        _hold(album, f"Its update now reaches {level.value}")
        return False
    if album.state == AlbumState.INCOMPLETE:
        # Writing a short album's tags needs the tagger's incomplete mode, which
        # is a decision about that album (#252) and not one to take unattended.
        _hold(album, "Tracks are missing, so it is applied from its page")
        return False
    # As the album page passes them, so the fingerprint is taken under exactly
    # the spelling settings `tag_with_release` will check it against.
    paths = list(album.folders)
    tagging = library_titles.choices(cfg.tagging.choices(), excluding=paths)
    reviewed = retag.release_fingerprint(release, mbid, tagging)
    with activity_store.action():
        try:
            retag.tag_with_release(
                album.path,
                mbid,
                cfg,
                tagger,
                # A pregap the user counted as present is written as the page
                # writes it (#242); otherwise the file-count guard applies.
                incomplete=bool(album.included_pregap_count),
                paths=paths,
                artwork_included=False,
                expected_release=reviewed,
            )
        except tagger_mod.TrackAssignmentRequired:
            _hold(album, "Track assignments need review")
            return False
        except (retag.ConfirmationChanged, tagger_mod.TagMismatchError) as e:
            _hold(album, str(e))
            return False
        except Exception as e:
            # Boundary catch, per album: one album that cannot be written must not
            # stop the rest, and nobody is watching this pass. Loud, with the
            # traceback; the album is held, so the Inbox says so too, and it is
            # not retried until MusicBrainz moves.
            log.exception("auto-apply: could not write %s", album.path)
            _hold(album, f"Couldn't be written: {e}")
            return False
        # Read after the write: tagging can move an album's id (event-recording §2).
        activity.record(
            f"Tag update applied automatically ({level.value})",
            album_id=sidecar_mod.album_id_for(album.path),
            album_label=tagger_mod.album_label(release, album.path),
        )
    _release_hold(album)
    gardener.refresh_flag(album, release)
    return True
