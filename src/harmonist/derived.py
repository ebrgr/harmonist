"""Which images Harmonist squished to fit, and from what (#888).

A squished copy is a different file from the image that was picked, so every
question of the form "does the album carry this image?" has to count a copy as
its original: a replaced front (#886), a choice already honoured, a stand-in
(#663), a watch's proposal. The pairs are durable in `activity_store`; this
holds them in memory, because the Library asks for every album on every
status poll.

Read once and kept up to date as squishes are recorded. A store that can't be
read is asked again on the next call rather than remembered as empty.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterable, Mapping

from . import activity_store

log = logging.getLogger(__name__)

_lock = threading.Lock()
_known: dict[str, str] | None = None


def originals() -> Mapping[str, str]:
    """Each squished image's digest, mapped to its original's.

    Empty, and logged, when the store can't be read: copies then read as
    images of their own until it can, which shows as an update still to apply
    rather than hiding one."""
    global _known
    with _lock:
        if _known is not None:
            return _known
        try:
            _known = activity_store.derived_images()
        except activity_store.StoreUnavailableError:
            log.exception(
                "could not read which images were squished — each squished copy "
                "reads as an image of its own until the store can be read"
            )
            return {}
        return _known


def record(digest: str, *, original: str) -> None:
    """Remember that `digest` was squished from `original`, durably."""
    activity_store.record_derived_image(digest, original=original)
    with _lock:
        if _known is not None:
            _known[digest] = original


def carried(digests: Iterable[str]) -> set[str]:
    """`digests`, with the original of each squished copy among them: what an
    album carrying them carries, for the questions that ask by digest."""
    known = originals()
    out = set(digests)
    out |= {known[d] for d in out if d in known}
    return out


def reset() -> None:
    """Forget what was read, so the next call reads the store (tests)."""
    global _known
    with _lock:
        _known = None
