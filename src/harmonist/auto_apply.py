"""Applying the tag updates nobody has to review (#273), and the ones the user
selected in the Inbox (#814), which are written the same way.

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
from .models import Album, AlbumState, Release

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
# The selection `write_claimed` is working through, by album id, so each Inbox
# card can say whether it is waiting its turn or being written.
_queue: dict[str, str] = {}
_queue_lock = threading.Lock()


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
    # A pregap the user counted as present is written as the page writes it
    # (#242); otherwise the file-count guard applies.
    failure = _write(
        album,
        mbid,
        release,
        cfg,
        tagger,
        incomplete=bool(album.included_pregap_count),
        outcome=f"Tag update applied automatically ({level.value})",
    )
    if failure is not None:
        # Held, so the Inbox says why, and not retried until MusicBrainz moves.
        _hold(album, failure)
        return False
    _release_hold(album)
    return True


def _write(
    album: Album,
    mbid: str,
    release: Release,
    cfg: config_mod.Config,
    tagger: tagger_mod.Tagger,
    *,
    incomplete: bool,
    outcome: str,
    report_failure: bool = False,
) -> str | None:
    """Write `album`'s update from `release`, tags only, as one action with its
    own History entry. None when written; otherwise why not, recorded in
    Activity too when `report_failure` says nobody else will say it."""
    # As the album page passes them, so the fingerprint is taken under exactly
    # the spelling settings `tag_with_release` will check it against.
    paths = list(album.folders)
    tagging = library_titles.choices(cfg.tagging.choices(), excluding=paths)
    reviewed = retag.release_fingerprint(release, mbid, tagging)
    failure: str | None = None
    with activity_store.action():
        try:
            retag.tag_with_release(
                album.path,
                mbid,
                cfg,
                tagger,
                incomplete=incomplete,
                paths=paths,
                artwork_included=False,
                expected_release=reviewed,
            )
        except tagger_mod.TrackAssignmentRequired:
            failure = "Track assignments need review"
        except (retag.ConfirmationChanged, tagger_mod.TagMismatchError) as e:
            failure = str(e)
        except Exception as e:
            # Boundary catch, per album: one album that cannot be written must not
            # stop the rest, and nobody may be watching this pass. Loud, with the
            # traceback.
            log.exception("could not write the tag update for %s", album.path)
            failure = f"Couldn't be written: {e}"
        # Read after the write: tagging can move an album's id (event-recording §2).
        # Inside the action, so a failure sits with whatever part of it was written.
        album_id = sidecar_mod.album_id_for(album.path)
        album_label = tagger_mod.album_label(release, album.path)
        if failure is None:
            activity.record(outcome, album_id=album_id, album_label=album_label)
        elif report_failure:
            _report(failure, album_id=album_id, album_label=album_label)
    gardener.refresh_flag(album, release)
    return failure


def _report(failure: str, *, album_id: str | None, album_label: str | None) -> None:
    activity.warning(
        f"Tag update not applied: {failure}", album_id=album_id, album_label=album_label
    )


@dataclass(frozen=True)
class ChosenResult:
    applied: int
    failed: int


def claim(albums: Sequence[Album], chosen: Mapping[str, str | None]) -> list[Album] | None:
    """Take the pass for the albums the user selected in the Inbox (#814) and
    mark them queued, or None while another pass is writing: it may be working
    through the same albums. `write_claimed` must follow. Two calls rather than
    one so the caller can claim before handing the writing to a thread: the
    Inbox redrawn in between already shows the selection queued."""
    if not _pass_lock.acquire(blocking=False):
        return None
    todo = [a for a in albums if a.id in chosen]
    with _queue_lock:
        _queue.update({a.id: "queued" for a in todo})
    return todo


def write_claimed(
    todo: Sequence[Album],
    chosen: Mapping[str, str | None],
    cfg: config_mod.Config,
    tagger: tagger_mod.Tagger,
    ignored: Mapping[str, activity_store.IgnoredUpdate],
    *,
    on_processed: Callable[[Album], None] | None = None,
) -> ChosenResult:
    """Write the selected updates `claim` returned, each against the
    MusicBrainz version its card showed (`chosen`, album id to version), and
    release the pass.

    The selection is a review, so it is written as a reviewed confirmation is
    (#532): from the stored release, refused where that release has moved since,
    and whatever the apply threshold says. Tags only, as the pass above writes
    them, since the card shows no artwork. Never Structure, which the Inbox does
    not offer. A failure is reported in Activity rather than held: the user is
    there to try again.

    Blocking on the disk, so call it from a worker thread; `on_processed` as for
    `apply_due`.
    """
    try:
        applied = failed = 0
        with progress.job("Applying updates", total=len(todo)) as job:
            for album in job.items(todo, lambda a: a.label):
                with _queue_lock:
                    _queue[album.id] = "applying"
                try:
                    outcome = _apply_chosen_one(album, chosen[album.id], cfg, tagger, ignored)
                    if outcome is True:
                        applied += 1
                    elif outcome is not None:
                        failed += 1
                finally:
                    # Before the refresh, so the Inbox it prompts no longer
                    # marks this album as waiting its turn.
                    with _queue_lock:
                        _queue.pop(album.id, None)
                    if on_processed is not None:
                        on_processed(album)
            job.closing = _chosen_summary(applied, failed)
        log.info("bulk apply: %d album(s) updated, %d not", applied, failed)
        return ChosenResult(applied=applied, failed=failed)
    finally:
        with _queue_lock:
            _queue.clear()
        _pass_lock.release()


def is_applying() -> bool:
    """Whether a selection is being applied: the Inbox offers no other while it
    is, since one would be refused."""
    with _queue_lock:
        return bool(_queue)


def queued(album: Album) -> str | None:
    """Where this album stands in the selection being applied: "queued",
    "applying", or None when it is not part of one."""
    with _queue_lock:
        return _queue.get(album.id)


def _chosen_summary(applied: int, failed: int) -> str:
    if not applied and not failed:
        return "Nothing to apply: already up to date"
    done = f"{applied} album updated" if applied == 1 else f"{applied} albums updated"
    return f"{done}, {failed} couldn't be: see Activity" if failed else done


def _apply_chosen_one(
    album: Album,
    version: str | None,
    cfg: config_mod.Config,
    tagger: tagger_mod.Tagger,
    ignored: Mapping[str, activity_store.IgnoredUpdate],
) -> bool | str | None:
    """True when written; a reason, already reported, when not; None when there
    was nothing left to write."""

    def refuse(reason: str) -> str:
        _report(reason, album_id=album.id, album_label=album.label)
        return reason

    shown = gardener.waiting_at(album, ignored)
    if shown is None:
        return None  # applied, ignored or caught up since it was selected
    if album.mb_version != version:
        return refuse("MusicBrainz changed after it was selected")
    if shown == owned.Significance.STRUCTURE:
        return refuse("A tracklist change is applied from its page")
    sc = album.sidecar
    mbid = sc.mb_release_id if sc else None
    release = mb_cache.stored_release(mbid) if mbid else None
    if mbid is None or release is None:
        return refuse("Its MusicBrainz release isn't stored, so it is applied from its page")
    # Re-judged at write time, as the pass above re-judges: the card can be
    # older than the files and the stored release beside it.
    gardener.refresh_flag(album, release)
    if not album.update_available or album.update_significance is None:
        return None
    if album.mb_version != version:
        return refuse("MusicBrainz changed after it was selected")
    level = owned.Significance(album.update_significance)
    if owned.ranked(level) > owned.ranked(shown):
        return refuse(f"Its update now reaches {level.value}")
    failure = _write(
        album,
        mbid,
        release,
        cfg,
        tagger,
        # As the album page's own Apply writes it: a short album in incomplete
        # mode, a counted pregap as present (#242).
        incomplete=album.state == AlbumState.INCOMPLETE or bool(album.included_pregap_count),
        outcome=f"Tag update applied ({level.value})",
        report_failure=True,
    )
    return True if failure is None else failure
