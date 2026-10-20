"""Browser test for applying Inbox tag updates from a selection (#814).

The selection lives in the page and has to survive the Inbox's background
refreshes, which redraw every box unticked; the Apply button and its count
follow the ticks by CSS and script; and each applied card leaves the list as
its album is written. None of that is visible to a route test, which sees only
the markup.

Opt-in: requires playwright (`pip install -e .[e2e]` + `playwright install
chromium`) and RUN_E2E=1. Run via `make e2e`.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_E2E") != "1", reason="e2e disabled (set RUN_E2E=1)"
)
playwright_sync = pytest.importorskip("playwright.sync_api")

#: Albums with an update on `bulk_apply_server`: the demo's own, and the two
#: its app module drifts. The demo's data decides each one's level; the test
#: works on whichever land in Enrichment, so long as there are several.
ALBUMS = ("demo-rel-blues-brothers", "demo-rel-barryjive", "demo-rel-sex-bob-omb")


def test_a_selection_survives_a_refresh_and_each_album_leaves_once_applied(
    bulk_apply_server: str,
) -> None:
    base = bulk_apply_server
    with playwright_sync.sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        # Every animation frozen at its start, as in a page that isn't being
        # drawn: the fold must not be what takes an applied card out.
        page.add_init_script(
            "const animate = Element.prototype.animate;"
            "Element.prototype.animate = function (...args) {"
            "  const animation = animate.apply(this, args); animation.pause(); return animation;"
            "};"
        )
        expect = playwright_sync.expect

        # Every level to the Inbox, so the Enrichment updates are listed.
        page.goto(f"{base}/settings")
        page.locator(
            '.update-routing__row[data-level="settings"] label:has(.update-routing__review)'
        ).click()
        with page.expect_response(
            lambda r: r.url.endswith("/settings") and r.request.method == "POST"
        ):
            page.get_by_role("button", name="Save settings", exact=True).click()
        # Looking at an album is what flags its update in the demo.
        for album in ALBUMS:
            page.request.get(f"{base}/library/{album}/compare", headers={"HX-Request": "true"})

        page.goto(f"{base}/?tab=inbox")
        group = page.locator("#update-group-enrichment")
        cards = group.locator(".update-card")
        picks = group.locator(".update-pick")
        apply = group.locator(".update-group__apply")
        expect(picks.first).to_be_visible()
        ids = cards.evaluate_all("cards => cards.map(card => card.id)")
        assert len(ids) >= 2, ids
        expect(apply).to_be_hidden()

        # One ticked: the button appears, and select-all reads as partial.
        picks.first.check()
        expect(apply).to_have_text("Apply 1 update", ignore_case=True)
        assert group.locator(".update-group__all").evaluate("box => box.indeterminate")

        group.locator(".update-group__all").check()
        expect(apply).to_have_text(f"Apply {len(ids)} updates", ignore_case=True)

        # The list re-renders every box unticked; the selection must stay.
        with page.expect_response(lambda r: r.url.endswith("/tasks")):
            page.evaluate("document.body.dispatchEvent(new Event('tasks-changed'))")
        for i in range(len(ids)):
            expect(picks.nth(i)).to_be_checked()
        expect(apply).to_be_visible()

        with page.expect_response(lambda r: r.url.endswith("/updates/apply")):
            apply.click()
        for card_id in ids:
            expect(page.locator(f"#{card_id}")).to_have_count(0)
        expect(page.locator("#update-group-enrichment")).to_have_count(0)

        page.goto(f"{base}/?tab=activity")
        expect(page.get_by_text("Tag update applied (enrichment)")).to_have_count(len(ids))

        browser.close()
