"""Whether the web server is serving a request right now (#793).

The background passes that read the whole library — the startup update check
and its re-checks — are pure Python and hold the GIL while they work, so a
request arriving mid-pass is served at a fraction of the machine (#299). They
used to answer that by resting three times as long as they worked, always. On a
NAS that turned five minutes of work into twenty, every restart, even with no
one there to be in the way of.

What actually needs the machine is a request being served, and that can be
counted. The web layer brackets each one in `serving`; a pass asks
`wait_for_idle` between albums. A request then waits for at most the rest of one
album, and a pass nobody is waiting on runs at the speed of its work.

In flight, never "recently": the status bar polls every 1.5 s on every page, so
any window long enough to cover a page's follow-up requests would cover all of
an open tab's time too, and the pass would never count the UI as idle.

Deliberately standalone, with no web import, so the passes can ask it without
depending on the web layer.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta

_lock = threading.Lock()
_in_flight = 0
# Set exactly while nothing is in flight, so a waiter wakes the moment the last
# request ends rather than on its next poll.
_idle = threading.Event()
_idle.set()


@contextmanager
def serving() -> Iterator[None]:
    """Count one request as in flight for the duration of the block."""
    global _in_flight
    with _lock:
        _in_flight += 1
        _idle.clear()
    try:
        yield
    finally:
        with _lock:
            _in_flight -= 1
            if _in_flight == 0:
                _idle.set()


def wait_for_idle(limit: timedelta) -> bool:
    """Block until no request is in flight, or `limit` has passed. Returns
    whether it is idle.

    Bounded, so one request that never finishes — a client that stopped
    reading, a handler stuck on a dead mount — slows a pass to one album per
    `limit` rather than parking it for good.
    """
    return _idle.wait(limit.total_seconds())
