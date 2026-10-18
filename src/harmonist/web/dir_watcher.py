"""Filesystem watcher that triggers a library rescan on external changes.

Files added or removed outside the app (copied straight into the music dir,
deleted by hand) don't pass through the rescan-after-mutation middleware, so
without this they'd stay invisible until the next in-app action. A `watchfiles`
watcher bridges that gap: it calls `on_change` (wired to
`ScanRunner.request_refresh`) with the paths that changed. Known albums can
then be refreshed without walking the rest of the library.

The watcher fails soft: any setup error (missing dir, watch-limit exhaustion)
is logged and the task exits without taking the app down. Why that's
acceptable, and what covers network mounts it can't see, is in
docs/design/staying-current.md.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

from watchfiles import awatch

log = logging.getLogger(__name__)


async def watch_music_dir(
    music_dir: Path,
    on_change: Callable[[set[Path]], None],
    *,
    settle: timedelta = timedelta(seconds=5),
    stop_event: asyncio.Event | None = None,
) -> None:
    """Deliver changed paths once `music_dir` has been quiet for `settle`.

    Each change (re)starts a settle timer; the rescan fires only after the tree
    has been idle for the full delay, so copying a batch of files in produces a
    single refresh instead of reading mid-copy. Runs until cancelled or
    `stop_event` is set; never raises into the caller (failures are logged and
    end the task)."""
    if not music_dir.is_dir():
        log.warning("File watcher disabled: %s is not a directory", music_dir)
        return

    loop = asyncio.get_running_loop()
    settle_seconds = settle.total_seconds()
    pending: asyncio.TimerHandle | None = None
    changed_paths: set[Path] = set()

    def fire() -> None:
        paths = changed_paths.copy()
        changed_paths.clear()
        log.debug("Library settled after change — refreshing %d paths", len(paths))
        on_change(paths)

    log.info(
        "Watching %s for changes (refresh %.0fs after activity settles)",
        music_dir,
        settle_seconds,
    )
    try:
        # Small awatch debounce so the settle timer closely tracks real activity;
        # the user-facing quiescence delay is `settle_seconds`, enforced below.
        async for _changes in awatch(music_dir, stop_event=stop_event, debounce=200):
            # The demo keeps its history inside its resettable sandbox. Reading
            # an album can update that database; it is not a music-file change.
            if all(Path(path).name.startswith(".demo-activity.db") for _, path in _changes):
                continue
            changed_paths.update(
                Path(path)
                for _, path in _changes
                if not Path(path).name.startswith(".demo-activity.db")
            )
            log.debug("Change detected; restarting %.0fs settle timer", settle_seconds)
            if pending is not None:
                pending.cancel()
            pending = loop.call_later(settle_seconds, fire)
    except asyncio.CancelledError:
        raise  # normal shutdown — let it propagate
    except Exception:
        # e.g. inotify watch-limit exhaustion on a large tree, or an unsupported
        # filesystem. Degrade to no-op rather than crash the app.
        log.exception("File watcher stopped; manual edits won't auto-rescan")
    finally:
        if pending is not None:
            pending.cancel()
