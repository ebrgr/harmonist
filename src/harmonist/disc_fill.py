"""Which folder could fill a disc the review shows as missing (#876).

A release kept in several untagged folders is adopted as several albums, and the
scan can't put them back together: with no release-track ids or disc numbers
there is no evidence that they hold different tracks rather than two copies. The
review can, because there the user is looking at one release and one missing
disc. Nothing here is stored; a folder added to a review is part of its draft.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from . import mb_lookup
from .compare import LENGTH_TOLERANCE_MS
from .models import Album, AlbumState, Release
from .track_assignment import Panel

# An album that could still be anything: no release in its sidecar or its tags.
_UNMATCHED = frozenset({AlbumState.NEW, AlbumState.NEEDS_MBID})


@dataclass(frozen=True)
class Gap:
    """A disc of the release with no file paired to any of its tracks."""

    disc: int
    lengths: tuple[int | None, ...]


@dataclass(frozen=True)
class Candidate:
    album: Album
    # Every file is within the match tolerance of its track, in order: Exact,
    # applied to the gap rather than the release.
    fits: bool


def gaps(panel: Panel, release: Release) -> list[Gap]:
    """The release's audio discs that the review leaves entirely unpaired."""
    video = set(mb_lookup.video_media_of(release))
    paired = {m for d, m in zip(panel.disk_order, panel.mb_order, strict=True) if d is not None}
    discs: dict[int, list[int]] = {}
    for m, entry in enumerate(panel.mb):
        disc = int(entry.number.split(".")[0])
        if disc not in video:
            discs.setdefault(disc, []).append(m)
    return [
        Gap(disc, tuple(panel.mb[m].length for m in tracks))
        for disc, tracks in discs.items()
        if not paired.intersection(tracks)
    ]


def fits(gap: Gap, lengths: Sequence[int | None]) -> bool:
    """Whether a folder's files match the gap track for track.

    A track or file with no length can't vote, so it can't fit, as an album
    whose lengths are unknown is never Exact (docs/design/matching.md).
    """
    return len(lengths) == len(gap.lengths) and all(
        a is not None and b is not None and abs(a - b) <= LENGTH_TOLERANCE_MS
        for a, b in zip(lengths, gap.lengths, strict=True)
    )


def eligible(album: Album, library: Iterable[Album], exclude: set[Path]) -> list[Album]:
    """The albums that could be added to `album`'s review.

    Only unmatched ones: a folder whose tags or sidecar name a release is
    already somebody's, and a review is no place to take it from them.
    """
    return [
        a
        for a in library
        if a.id != album.id
        and a.state in _UNMATCHED
        and not a.has_tag_mbid
        and not (a.sidecar and a.sidecar.mb_release_id)
        and not exclude.intersection(a.folders)
    ]


def candidates(
    gap: Gap,
    albums: Iterable[Album],
    lengths_of: Callable[[Album], Sequence[int | None] | None],
) -> list[Candidate]:
    """Unmatched albums with as many files as the gap has tracks, best first.

    The count is the album's own, so only albums of the right size have their
    lengths looked at.
    """
    out = []
    for a in albums:
        if a.track_count != len(gap.lengths):
            continue
        lengths = lengths_of(a)
        out.append(Candidate(a, lengths is not None and fits(gap, lengths)))
    return sorted(out, key=lambda c: (not c.fits, str(c.album.path)))


def automatic(found: dict[int, list[Candidate]]) -> dict[int, Album]:
    """The discs a single folder fits, and nothing else.

    Unique or nothing, like Exact's tie rule: two folders that both fit are a
    choice for the user, and so is one folder that fits two discs.
    """
    only = {
        disc: fitting[0].album
        for disc, cands in found.items()
        if len(fitting := [c for c in cands if c.fits]) == 1
    }
    ids = [a.id for a in only.values()]
    return {disc: a for disc, a in only.items() if ids.count(a.id) == 1}
