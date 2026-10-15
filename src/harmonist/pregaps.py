"""Evidence for an owner's combined-rip decision; never an automatic assignment."""

from collections.abc import Sequence
from dataclasses import dataclass

from . import formats
from .models import IncludedPregap, Release

TOLERANCE_MS = 2000


@dataclass(frozen=True)
class Offer:
    decision: IncludedPregap
    disc: int
    pregap_slot: int
    first_slot: int
    disk_index: int
    pregap_length: int
    first_length: int


def offers(
    release: Release,
    tags: Sequence[formats.TrackTags],
    targets: Sequence[formats.TagSet],
    lengths: Sequence[int | None],
) -> tuple[Offer, ...]:
    result = []
    for medium in release.get("medium-list") or []:
        gap = medium.get("pregap")
        if not gap or not gap.get("id") or (gap.get("recording") or {}).get("video") == "true":
            continue
        gap_slots = [i for i, t in enumerate(targets) if t.mb_release_track_id == gap["id"]]
        if len(gap_slots) != 1:
            continue
        g = gap_slots[0]
        first = [
            i
            for i, t in enumerate(targets)
            if t.disc_num == targets[g].disc_num and t.track_num == 1
        ]
        if len(first) != 1:
            continue
        f = first[0]
        target = targets[f]
        if (
            not target.mb_release_track_id
            or sum(t.mb_release_track_id == target.mb_release_track_id for t in targets) != 1
            or any(
                t.get("id") == target.mb_release_track_id
                and (t.get("recording") or {}).get("video") == "true"
                for t in medium.get("track-list") or []
            )
        ):
            continue
        # A numbered track zero also rules out the offer when its IDs are absent.
        if any(
            t.release_track_id == gap["id"]
            or (t.disc_num or 1, t.track_num) == (target.disc_num, 0)
            for t in tags
        ):
            continue
        files = [i for i, t in enumerate(tags) if t.release_track_id == target.mb_release_track_id]
        if len(files) != 1:
            continue
        d = files[0]
        tag = tags[d]
        gl, fl, dl = lengths[g], lengths[f], tag.duration_ms
        if (
            tag.unreadable
            or tag.video
            or tag.owned.get("mb_album_id") != release["id"]
            or (tag.disc_num or 1, tag.track_num) != (target.disc_num, 1)
            or gl is None
            or fl is None
            or dl is None
            or min(gl, fl, dl) <= 0
            or abs(dl - gl - fl) > TOLERANCE_MS
            or abs(dl - fl) <= TOLERANCE_MS
        ):
            continue
        result.append(
            Offer(
                IncludedPregap(
                    release["id"], gap["id"], target.mb_release_track_id, dl, target.track_total
                ),
                target.disc_num,
                g,
                f,
                d,
                gl,
                fl,
            )
        )
    return tuple(result)


def effective(
    fields: Sequence[formats.ScanFields], decisions: Sequence[IncludedPregap], release: str | None
) -> tuple[IncludedPregap, ...]:
    """Credit only the reviewed rip, without a cache read or network request.

    Duration and total are the scope of the owner's decision, not replacement
    metadata. A changed duration or retagged layout needs a new decision.
    """
    result = []
    credited_discs: set[int] = set()
    for decision in decisions:
        if decision.release != release or any(
            f.release_track_id == decision.pregap for f in fields
        ):
            continue
        matches = [f for f in fields if f.release_track_id == decision.track]
        if len(matches) != 1:
            continue
        first = matches[0]
        disc = first.disc_num or 1
        on_disc = [f for f in fields if (f.disc_num or 1) == disc and not f.unreadable]
        if (
            first.unreadable
            or first.album_id != release
            or first.duration_ms != decision.duration_ms
            or not on_disc
            or any(f.track_total != decision.track_total for f in on_disc)
            or len(on_disc) >= decision.track_total
            or disc in credited_discs
        ):
            continue
        result.append(decision)
        credited_discs.add(disc)
    return tuple(result)


def effective_for_tags(
    tags: Sequence[formats.TrackTags], decisions: Sequence[IncludedPregap], release: str
) -> tuple[IncludedPregap, ...]:
    """The page and scanner apply the same decision to their already-read tags."""
    fields = [
        formats.ScanFields(
            None,
            t.owned.get("mb_album_id"),
            None,
            None,
            unreadable=t.unreadable,
            disc_num=t.disc_num,
            release_track_id=t.release_track_id,
            track_total=t.owned.get("track_total"),
            duration_ms=t.duration_ms,
        )
        for t in tags
    ]
    return effective(fields, decisions, release)
