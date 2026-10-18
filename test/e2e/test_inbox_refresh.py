"""Browser test for the Inbox list following its own count (#273).

The tab badge comes from the status bar's poll, and the list from `/tasks`. A
tag update can reach the Inbox with neither a scan nor a reconcile — the startup
update check flagging albums one by one is the case that was seen — and then the
badge climbed while the list stayed as it was when the page loaded. Whether a
moved count makes the list ask again is a question about when a request fires,
which only a browser answers.

The count is pinned by rewriting the poll's response rather than by preparing an
album whose flag moves on cue: what is under test is the page's reaction to the
number, and a real flag would also move whenever the demo's own startup work
happened to land.

Opt-in: requires playwright (`pip install -e .[e2e]` + `playwright install
chromium`) and RUN_E2E=1. Run via `make e2e`.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_E2E") != "1", reason="e2e disabled (set RUN_E2E=1)"
)
playwright_sync = pytest.importorskip("playwright.sync_api")


def _path_is(path: str) -> Any:
    return lambda r: urlparse(r if isinstance(r, str) else r.url).path == path


def test_the_inbox_list_reloads_when_its_count_moves(demo_server: str) -> None:
    """While the count holds, the idle Inbox asks for nothing; when it moves, the
    list asks `/tasks` again, once the badge shows the new number."""
    pinned = {"inbox": 40}

    def status(route: Any) -> None:
        response = route.fetch()
        data = response.json()
        data["counts"]["inbox"] = pinned["inbox"]
        route.fulfill(response=response, json=data)

    with playwright_sync.sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.route(_path_is("/status"), status)
        page.goto(demo_server)

        app_status = page.locator("#app-status")
        playwright_sync.expect(app_status).to_have_attribute("data-scan", "done")
        playwright_sync.expect(app_status).to_have_attribute("data-reconcile", "idle")
        playwright_sync.expect(page.locator("#task-list")).not_to_contain_text("Loading…")
        playwright_sync.expect(page.locator("#inbox-count")).to_have_text("40")

        tasks: list[str] = []
        page.on("request", lambda r: tasks.append(r.url) if _path_is("/tasks")(r) else None)
        for _ in range(3):
            with page.expect_response(_path_is("/status")):
                pass
        assert tasks == []

        with page.expect_request(_path_is("/tasks"), timeout=5_000):
            pinned["inbox"] = 41
        playwright_sync.expect(page.locator("#inbox-count")).to_have_text("41")

        browser.close()
