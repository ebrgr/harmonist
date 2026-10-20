"""Browser test for an Inbox update card's Show all / Show fewer (#801).

The full change list replaces the lead rows by CSS off the button's
`aria-expanded`, and a script keeps a card expanded across the list's refreshes.
Neither is visible to a route test, which sees only the markup.

Opt-in: requires playwright (`pip install -e .[e2e]` + `playwright install
chromium`) and RUN_E2E=1. Run via `make e2e`.
"""

from __future__ import annotations

import os
import re

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_E2E") != "1", reason="e2e disabled (set RUN_E2E=1)"
)
playwright_sync = pytest.importorskip("playwright.sync_api")


def test_show_all_swaps_the_lead_for_the_full_list_and_survives_a_refresh(
    browser_factory,
    demo_server: str,
) -> None:
    browser = browser_factory("chromium")
    page = browser.new_page()
    page.set_default_timeout(5000)

    # Every level to the Inbox, so the demo's Enrichment update is listed.
    page.goto(f"{demo_server}/settings")
    page.locator(
        '.update-routing__row[data-level="settings"] label:has(.update-routing__review)'
    ).click()
    with page.expect_response(lambda r: r.url.endswith("/settings") and r.request.method == "POST"):
        page.get_by_role("button", name="Save settings", exact=True).click()
    # Looking at the album is what flags its update in the demo.
    page.request.get(
        f"{demo_server}/library/demo-rel-blues-brothers/compare",
        headers={"HX-Request": "true"},
    )

    page.goto(f"{demo_server}/?tab=inbox")
    card = page.locator("#task-demo-rel-blues-brothers")
    button = card.locator(".update-card__more")
    show = button.locator(".update-card__more-show")
    fewer = button.locator(".update-card__more-hide")
    lead = card.locator(".update-card__lead")
    full = card.locator(".update-card__all")
    playwright_sync.expect(show).to_have_text(re.compile(r"Show all \d+ changes"))
    playwright_sync.expect(show).to_be_visible()
    playwright_sync.expect(fewer).to_be_hidden()
    playwright_sync.expect(lead).to_be_visible()
    playwright_sync.expect(full).to_be_hidden()

    button.click()
    playwright_sync.expect(button).to_have_attribute("aria-expanded", "true")
    playwright_sync.expect(fewer).to_be_visible()
    playwright_sync.expect(show).to_be_hidden()
    playwright_sync.expect(lead).to_be_hidden()
    playwright_sync.expect(full).to_be_visible()

    # The list re-renders every card collapsed; this one must stay open.
    with page.expect_response(lambda r: r.url.endswith("/tasks")):
        page.evaluate("document.body.dispatchEvent(new Event('tasks-changed'))")
    playwright_sync.expect(button).to_have_attribute("aria-expanded", "true")
    playwright_sync.expect(full).to_be_visible()

    button.click()
    playwright_sync.expect(show).to_be_visible()
    playwright_sync.expect(lead).to_be_visible()
    playwright_sync.expect(full).to_be_hidden()

    page.context.close()
