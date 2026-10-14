"""Library artwork findings and paced, review-only CAA discovery (#269, #276).

Scans retain descriptions, never image bytes. The shared artwork plan judges
those descriptions against the archive observation in memory. Filtering is pure;
only warming and checking read the durable cache, and only checking uses CAA.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import (
    activity_store,
    artwork,
    artwork_watch,
    caa_cache,
    cover_art,
    formats,
    mb_cache,
    mb_lookup,
)
from .models import Album, AlbumState

log = logging.getLogger(__name__)
TICK = timedelta(minutes=10)
_WINDOW = timedelta(days=1)
_RETRY = timedelta(hours=1)


@dataclass(frozen=True)
class LocalArtwork:
    tracks: tuple[tuple[Path, formats.EmbeddedArt | None], ...]
    cover: artwork.FolderCover | None
    unreadable: bool = False

    @property
    def missing(self) -> bool:
        return not self.unreadable and self.cover is None and not any(a for _, a in self.tracks)

    @property
    def missing_tracks(self) -> bool:
        return not self.unreadable and any(a is None for _, a in self.tracks)

    @property
    def different_tracks(self) -> bool:
        return not self.unreadable and artwork.has_per_track_art(
            a.digest if a else None for _, a in self.tracks
        )


@dataclass(frozen=True)
class Observation:
    answer: activity_store.CachedCoverArt | None
    image: formats.EmbeddedArt | None


_observed: dict[str, Observation] = {}
_attempted: dict[str, datetime] = {}
_revision = 0
_policy: Callable[[], artwork.FolderCoverPolicy] = lambda: artwork.FolderCoverPolicy.NEVER


def configure(policy: Callable[[], artwork.FolderCoverPolicy]) -> None:
    global _policy, _revision
    _policy = policy
    _observed.clear()
    _attempted.clear()
    _revision = 0
    artwork_watch.reset()


def revision() -> int:
    """A changed candidate refreshes an open Library through its status poll."""
    return _revision


def read_local(
    files: Sequence[Path], fields: Sequence[formats.ScanFields], cover_path: Path | None
) -> LocalArtwork:
    """Finish a scan's observation with one folder-image read on a cache miss."""
    unreadable = any(f.unreadable or (f.has_cover and f.art is None) for f in fields)
    cover = None
    if cover_path is not None:
        try:
            data = cover_path.read_bytes()
        except OSError:
            log.exception("could not assess artwork in %s", cover_path)
            unreadable = True
        else:
            cover = artwork.FolderCover(
                name=cover_path.name,
                path=cover_path,
                image=formats.EmbeddedArt.of(
                    data, "image/png" if cover_path.suffix.lower() == ".png" else "image/jpeg"
                ),
            )
    return LocalArtwork(
        tuple((p, f.art) for p, f in zip(files, fields, strict=True) if not f.unreadable),
        cover,
        unreadable,
    )


def observe(mbid: str) -> None:
    """Publish one durable CAA answer and its cached original; no network."""
    answer = caa_cache.stored(mbid)
    front = cover_art.cached_front(mbid)
    global _revision
    observation = Observation(
        answer,
        formats.EmbeddedArt.of(front.data, front.mime) if front is not None else None,
    )
    previous = _observed.get(mbid)
    _observed[mbid] = observation
    watch_changed = artwork_watch.observe(mbid)
    if (
        watch_changed
        or (previous.image if previous else None) != observation.image
        or (
            bool(previous and previous.answer and previous.answer.from_release_group)
            != bool(answer and answer.from_release_group)
        )
    ):
        _revision += 1


def warm(albums: Sequence[Album]) -> None:
    for mbid in dict.fromkeys(a.sidecar.mb_release_id for a in albums if a.sidecar):
        if mbid and mbid not in _observed:
            observe(mbid)


def operation(album: Album) -> artwork.Operation | None:
    """The default review's operation, independent of tag significance/ignores."""
    local = album.artwork_local
    if local is None or local.unreadable:
        return None
    sc = album.sidecar
    observed = _observed.get(sc.mb_release_id) if sc and sc.mb_release_id else None
    image = observed.image if observed else None
    own = (
        observed
        and observed.answer
        and observed.answer.has_art
        and not observed.answer.from_release_group
    )
    borrowed = (
        frozenset(b.digest for b in sc.borrowed_artwork if b.release == sc.mb_release_id)
        if sc and own
        else frozenset()
    )
    choices, picks = artwork_watch.proposal(sc, local.tracks, local.cover, _policy())
    return artwork.plan(
        album.path,
        local.tracks,
        local.cover,
        image,
        stand_ins=borrowed,
        choices=choices,
        picks=picks,
        excluded_archive=artwork_watch.excluded(sc, image),
        folder_cover=_policy(),
    ).operation(artwork.Scope.ALL)


def threshold(album: Album) -> int | None:
    """Width a candidate must exceed to be useful; -1 means any image.

    A stand-in gives way regardless of dimensions. Missing images can also
    take an unmeasurable candidate. Existing embedded art never needs enlarging.
    """
    local = album.artwork_local
    sc = album.sidecar
    borrowed = (
        frozenset(b.digest for b in sc.borrowed_artwork if b.release == sc.mb_release_id)
        if sc
        else frozenset()
    )
    return download_threshold(local, borrowed)


def download_threshold(local: LocalArtwork | None, borrowed: frozenset[str]) -> int | None:
    """Shared by discovery and the album page: fetch what the plan could use."""
    if local is None:
        return -1
    if local.unreadable:
        return None
    carried = {a.digest for _, a in local.tracks if a}
    if local.cover:
        carried.add(local.cover.image.digest)
    if carried & borrowed:
        return -1
    if local.cover is None:
        return -1 if local.missing or _policy() is artwork.FolderCoverPolicy.IF_MISSING else None
    if artwork.has_per_track_art(a.digest if a else None for _, a in local.tracks):
        return None
    size = local.cover.image.size
    return size.width if size else None


def sweep(albums: Sequence[Album], *, limit: int | None = None) -> int:
    """Refresh a bounded oldest-first slice on CAA's own clock. Never write music.

    Attempts also pace failures and evicted images, which cannot advance the
    durable observation date. Stop at the first service failure so an outage
    costs one failing request per tick, not one per album. MusicBrainz is read
    from its cache except when a watch's release-group identity needs refilling;
    its age never gates CAA work.
    """
    now = datetime.now(UTC)
    groups: dict[str, list[Album]] = {}
    for album in albums:
        if (
            (
                album.state in {AlbumState.COMPLETE, AlbumState.INCOMPLETE}
                or artwork_watch.active(album.sidecar)
            )
            and album.sidecar
            and album.sidecar.mb_release_id
        ):
            groups.setdefault(album.sidecar.mb_release_id, []).append(album)
    floor = datetime.min.replace(tzinfo=UTC)
    due: list[tuple[datetime, str, int | None]] = []
    for mbid, copies in groups.items():
        attempted = _attempted.get(mbid)
        if attempted and timedelta(0) <= now - attempted < _RETRY:
            continue
        width = min((w for a in copies if (w := threshold(a)) is not None), default=None)
        watching = any(artwork_watch.active(a.sidecar) for a in copies)
        if caa_cache.front_due(mbid, keep_if_wider_than=width) or (
            watching and artwork_watch.due(mbid)
        ):
            known = caa_cache.stored(mbid)
            due.append((max(known.fetched_at if known else floor, attempted or floor), mbid, width))
    due.sort()
    count = limit if limit is not None else math.ceil(len(due) * TICK / _WINDOW)
    deadline = time.monotonic() + TICK.total_seconds() / 2
    checked = 0
    for _, mbid, width in due[:count]:
        if time.monotonic() >= deadline:
            break
        _attempted[mbid] = datetime.now(UTC)
        release = mb_cache.stored_release(mbid)
        group = (release.get("release-group") or {}).get("id") if release else None
        try:
            if any(artwork_watch.active(a.sidecar) for a in groups[mbid]):
                artwork_watch.check(mbid)
                artwork_watch.prepare(groups[mbid])
            caa_cache.front(
                mbid,
                release_group_mbid=group if isinstance(group, str) else None,
                keep_if_wider_than=width,
            )
        except (cover_art.CoverArtError, mb_lookup.MBError):
            log.exception("background artwork check failed for %s; will retry later", mbid)
            break
        observe(mbid)
        checked += 1
    return checked
