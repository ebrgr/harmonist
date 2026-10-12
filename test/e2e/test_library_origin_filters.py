"""Origin menu navigation stays a single filter across Library interactions."""

import os
import re

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("RUN_E2E") != "1", reason="e2e disabled")
pw = pytest.importorskip("playwright.sync_api")


@pytest.mark.parametrize(("engine", "width"), [("chromium", 1280), ("webkit", 390)])
def test_origin_menu_navigation(reset_demo_server, htmx_ready, engine, width):
    with pw.sync_playwright() as playwright:
        browser = getattr(playwright, engine).launch()
        page = browser.new_page(viewport={"width": width, "height": 900})
        page.goto(f"{reset_demo_server}/?tab=library&filter=no-artwork&limit=1")
        trigger = htmx_ready(page.locator("#library-origin-button"))
        menu = page.get_by_role("navigation", name="Origin filters", exact=True)
        trigger.wait_for(state="visible")
        page.evaluate("window.originNoReload = true")
        trigger.press("Enter")
        pw.expect(menu).to_be_visible()
        box = menu.bounding_box()
        assert 0 <= box["x"] and box["x"] + box["width"] <= width
        pw.expect(menu.locator('[aria-disabled="true"]', has_text="Qobuz")).to_be_visible()
        trigger.press("Escape")
        pw.expect(menu).to_be_hidden()
        pw.expect(trigger).to_be_focused()
        trigger.click()
        page.get_by_role("searchbox", name="Search the library").click()
        pw.expect(menu).to_be_hidden()
        trigger.click()
        htmx_ready(menu.get_by_role("link", name=re.compile("^Unknown"))).press("Enter")
        pw.expect(page).to_have_url(re.compile("filter=origin-unknown"))
        assert "filter=no-artwork" not in page.url
        assert page.evaluate("window.originNoReload === true")
        pw.expect(trigger).to_contain_text("Origin: Unknown")
        pw.expect(menu).to_be_hidden()
        trigger.click()
        pw.expect(menu.locator('[aria-current="true"]')).to_contain_text("Unknown")
        trigger.press("Escape")

        htmx_ready(page.get_by_role("link", name="Next →", exact=True)).click()
        pw.expect(page.locator("#library-page")).to_have_attribute("data-page", "2")
        pw.expect(page).to_have_url(re.compile("filter=origin-unknown"))
        htmx_ready(page.get_by_label("Show", exact=True)).select_option("20")
        pw.expect(page.locator("#library-page")).to_have_attribute("data-limit", "20")
        pw.expect(trigger).to_contain_text("Origin: Unknown")

        page.get_by_role("searchbox", name="Search the library").fill("Rural")
        htmx_ready(page.get_by_role("button", name="Search", exact=True)).click()
        pw.expect(page).to_have_url(re.compile("filter=origin-unknown&q=Rural"))
        pw.expect(trigger).to_have_text(re.compile(r"Origin: Unknown\s+1"))
        htmx_ready(trigger).wait_for(state="visible")
        with page.expect_response(lambda r: "/library?" in r.url and "origin-unknown" in r.url):
            page.evaluate("document.body.dispatchEvent(new Event('library-refresh'))")
        pw.expect(trigger).to_contain_text("Origin: Unknown")
        assert page.evaluate("window.originNoReload === true")

        htmx_ready(page.locator("#lib-demo-rel-rural-juror")).click()
        pw.expect(page.locator("dd[data-field='origin']")).to_have_text("Unknown")
        page.get_by_role("link", name="← Back to library", exact=True).click()
        pw.expect(page).to_have_url(re.compile("filter=origin-unknown&q=Rural"))
        page.reload()
        pw.expect(trigger).to_contain_text("Origin: Unknown")
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")

        # A bookmarked selection remains clearable when its search has no hits.
        page.get_by_role("searchbox", name="Search the library").fill("no-such-album")
        htmx_ready(page.get_by_role("button", name="Search", exact=True)).click()
        pw.expect(trigger).to_have_text(re.compile(r"Origin: Unknown\s+0"))
        trigger.click()
        pw.expect(menu.locator('[aria-current="true"]')).to_contain_text("Unknown")
        trigger.press("Escape")
        filters = page.get_by_role("navigation", name="Library filters", exact=True)
        htmx_ready(filters.get_by_role("link", name=re.compile("^All"))).click()
        pw.expect(trigger).to_have_text("Origin")
        pw.expect(page).not_to_have_url(re.compile("filter="))
        pw.expect(page).to_have_url(re.compile("q=no-such-album"))
        browser.close()
