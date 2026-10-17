"""Pressing "read again" while the page's own comparison is loading (#775).

The button is on the page from the first paint, but its `hx-sync` queues a click
behind the page's own `/compare` fetch, and that fetch's response swaps the
button's block out of band. HTMX drops a queued request whose element has left
the document, so a click in that window went nowhere: no request, no error, a
button that looked as if it had worked. The #40 shape, on the escape hatch out
of a stale comparison.

The window is the length of one MusicBrainz read, so holding that read open
makes the race deterministic instead of a matter of how busy the machine is.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_E2E") != "1", reason="e2e disabled (set RUN_E2E=1)"
)
playwright_sync = pytest.importorskip("playwright.sync_api")

ALBUM = "demo-rel-dingoes"


def test_read_again_while_the_first_comparison_loads_is_not_dropped(
    demo_server: str, htmx_ready
) -> None:
    with playwright_sync.sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()

        # The page's own fetch carries no query string, so this holds it and
        # lets the forced read through.
        held = []
        page.route(f"**/library/{ALBUM}/compare", lambda route: held.append(route))
        with page.expect_request(lambda r: r.url.endswith(f"/library/{ALBUM}/compare")):
            page.goto(f"{demo_server}/album/{ALBUM}")

        button = page.locator("#album-tags > header").get_by_role(
            "button", name="Read this release from MusicBrainz again"
        )
        htmx_ready(button).click()

        assert len(held) == 1, "the first comparison is still in flight"
        with page.expect_response(lambda r: "reread=1" in r.url, timeout=10_000) as reread:
            held.pop().continue_()
        assert reread.value.ok

        playwright_sync.expect(button).to_be_enabled()
        playwright_sync.expect(page.locator(f"#compare-{ALBUM} .tag-fields")).to_be_visible()

        browser.close()
