"""Artwork choices survive HTMX navigation and remain escapable when empty."""

import os
import re

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("RUN_E2E") != "1", reason="e2e disabled")
pw = pytest.importorskip("playwright.sync_api")


@pytest.mark.parametrize(("engine", "width"), [("chromium", 1280), ("webkit", 390)])
def test_artwork_menu(library_filters_server, htmx_ready, engine, width):
    with pw.sync_playwright() as playwright:
        browser = getattr(playwright, engine).launch()
        page = browser.new_page(viewport={"width": width, "height": 900})
        # This real check populates the shared CAA observation for an artless album.
        page.goto(f"{library_filters_server}/album/demo-rel-rural-juror")
        pw.expect(
            page.locator("#album-artwork-note-demo-rel-rural-juror .sev--artwork")
        ).to_contain_text("Addition")
        page.get_by_role("link", name="← Back to library", exact=True).click()
        trigger = page.locator("#library-artwork-button")
        menu = page.get_by_role("navigation", name="Artwork filters", exact=True)
        page.evaluate("window.artworkNoReload = true")
        htmx_ready(trigger).press("Enter")
        pw.expect(menu).to_be_visible()
        box = menu.bounding_box()
        assert 0 <= box["x"] and box["x"] + box["width"] <= width
        trigger.press("Escape")
        pw.expect(menu).to_be_hidden()
        pw.expect(trigger).to_be_focused()
        trigger.click()
        htmx_ready(menu.get_by_role("link", name=re.compile(r"^Addition"))).click()
        pw.expect(page).to_have_url(re.compile(r"filter=artwork-addition"))
        pw.expect(trigger).to_contain_text("Artwork: Addition")
        pw.expect(page.locator("#lib-demo-rel-rural-juror .sev--artwork")).to_contain_text(
            "Addition"
        )
        assert page.evaluate("window.artworkNoReload === true")
        trigger.click()
        pw.expect(menu.locator('[aria-current="true"]')).to_contain_text("Addition")
        assert menu.locator('[aria-current="true"] svg').count() == 1
        htmx_ready(menu.get_by_role("link", name=re.compile(r"^No artwork"))).click()
        pw.expect(page).to_have_url(re.compile(r"filter=no-artwork"))
        page.reload()
        pw.expect(trigger).to_contain_text("Artwork: No artwork")
        page.get_by_role("searchbox").fill("no-such-album")
        htmx_ready(page.get_by_role("button", name="Search", exact=True)).click()
        pw.expect(page.locator("#library-result-count")).to_have_text("0 albums")
        htmx_ready(trigger).click()
        htmx_ready(menu.get_by_role("link", name="Any artwork status", exact=True)).click()
        pw.expect(page).not_to_have_url(re.compile(r"filter="))
        pw.expect(page).to_have_url(re.compile(r"q=no-such-album"))
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        browser.close()
