"""How long a line stays in the status bar (#816).

A report — an action's flash, a job's closing line, the last sync — clears
itself after a few seconds, while an error stays; and the poll sets "Last sync"
only when a sync ends, rather than writing it over whatever came after. Both are
timers and poll ordering in the page's script, which no rendered-HTML test can
see. The clock is Playwright's, so the test waits for no real lifetime.

Opt-in: requires playwright (`pip install -e .[e2e]` + `playwright install
chromium`) and RUN_E2E=1. Run via `make e2e`.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_E2E") != "1", reason="e2e disabled (set RUN_E2E=1)"
)
pw = pytest.importorskip("playwright.sync_api")

# Longer than any report's lifetime in the bar.
_LONG_AFTER_MS = 60_000


def _flash(page: Any, verb: str, level: str) -> None:
    """Raise the event a route's `HX-Trigger: harmonist-status` raises."""
    page.evaluate(
        """([verb, level]) => document.body.dispatchEvent(new CustomEvent(
            'harmonist-status', {detail: {verb, details: 'Geogaddi', level}}))""",
        [verb, level],
    )


def _open(playwright: Any, base: str, sync: dict[str, Any]) -> tuple[Any, Any, list[int]]:
    """A page whose /status reports `sync` (updated in place by the caller), and
    a list that grows by one per poll answered."""
    browser = playwright.chromium.launch()
    page = browser.new_page()
    page.clock.install()
    polls: list[int] = []

    def status(route: Any) -> None:
        data = route.fetch().json()
        data["sync"].update(sync)
        polls.append(1)
        route.fulfill(json=data)

    page.route("**/status", status)
    page.goto(base)
    page.wait_for_function("() => document.getElementById('app-status').dataset.sync")
    return browser, page, polls


def _await_polls(page: Any, polls: list[int], n: int) -> None:
    target = len(polls) + n
    while len(polls) < target:
        page.wait_for_timeout(100)
    # The last answer's swap and its handler run after the route returns.
    page.wait_for_timeout(200)


def test_a_report_clears_itself_and_an_error_stays(demo_server: str) -> None:
    with pw.sync_playwright() as playwright:
        browser, page, _ = _open(playwright, demo_server, {})
        bar = page.locator("#harmonist-status")

        _flash(page, "Re-tagged", "info")
        pw.expect(bar).to_contain_text("Re-tagged")
        page.clock.run_for(_LONG_AFTER_MS)
        pw.expect(bar).to_be_empty()

        _flash(page, "Re-tag failed", "error")
        page.clock.run_for(_LONG_AFTER_MS)
        pw.expect(bar).to_contain_text("Re-tag failed")
        browser.close()


def test_last_sync_is_set_when_a_sync_ends_not_on_every_poll(demo_server: str) -> None:
    finished = {"state": "idle", "finished_at": "2026-10-20T09:00:00+00:00", "new_items": 2}
    with pw.sync_playwright() as playwright:
        browser, page, polls = _open(playwright, demo_server, finished)
        bar = page.locator("#harmonist-status")

        # A sync that ended before the page opened is not news on it.
        _await_polls(page, polls, 2)
        pw.expect(bar).not_to_contain_text("Last sync")

        # Nor does it replace a newer line on the next poll.
        _flash(page, "Re-tagged", "info")
        _await_polls(page, polls, 2)
        pw.expect(bar).to_contain_text("Re-tagged")

        # A sync ending while the page is open is.
        finished["finished_at"] = "2026-10-20T09:05:00+00:00"
        _await_polls(page, polls, 1)
        pw.expect(bar).to_contain_text("Last sync")
        pw.expect(bar).to_contain_text("2 new")
        browser.close()


def test_a_failed_sync_stays(demo_server: str) -> None:
    sync = {"state": "idle", "finished_at": None, "last_error": None}
    with pw.sync_playwright() as playwright:
        browser, page, polls = _open(playwright, demo_server, sync)
        bar = page.locator("#harmonist-status")
        _await_polls(page, polls, 1)

        sync.update(finished_at="2026-10-20T09:05:00+00:00", last_error="Bandcamp said no")
        _await_polls(page, polls, 1)
        pw.expect(bar).to_contain_text("Sync failed")
        page.clock.run_for(_LONG_AFTER_MS)
        pw.expect(bar).to_contain_text("Bandcamp said no")
        browser.close()
