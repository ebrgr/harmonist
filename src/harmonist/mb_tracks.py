"""MusicBrainz medium tracks, including the separately supplied pregap."""

from typing import Any

from .models import Track


def tracks_of(medium: dict[str, Any]) -> list[Track]:
    """The pregap precedes ordinary tracks without changing their positions."""
    tracks: list[Track] = list(medium.get("track-list") or [])
    if pregap := medium.get("pregap"):
        tracks.insert(0, pregap)
    return tracks


def track_total(medium: dict[str, Any]) -> int:
    """Picard's totaltracks: ordinary track-count plus a present pregap.

    Older/minimal payloads without track-count are sized from their track list.
    A malformed supplied count raises rather than pretending the disc is empty.
    """
    ordinary = medium.get("track-count")
    count = int(ordinary) if ordinary is not None else len(medium.get("track-list") or [])
    return count + bool(medium.get("pregap"))
