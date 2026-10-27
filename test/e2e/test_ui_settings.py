"""Settings controls navigation without disabling local artist profiles."""

import os
import re
from urllib.parse import parse_qs, urlsplit

import pytest
from test.e2e.conftest import _run_demo_server

pytestmark = pytest.mark.skipif(os.environ.get("RUN_E2E") != "1", reason="e2e disabled")
pw = pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def ui_server(tmp_path_factory):
    yield from _run_demo_server(
        tmp_path_factory.mktemp("ui-settings"), app_module="test.e2e.artists_app:app"
    )


@pytest.mark.parametrize("engine,width", [("chromium", 1280), ("webkit", 390)])
def test_default_hidden_tab_setting_history_and_artist_links(
    browser_factory, ui_server, engine, width
):
    page = browser_factory(engine).new_page(viewport={"width": width, "height": 900})
    page.goto(ui_server + "/")
    artists = page.get_by_role("button", name="Artists", exact=True)
    pw.expect(artists).to_have_count(0)
    page.evaluate("localStorage.setItem('harmonist-tab', 'artists')")
    page.reload()
    pw.expect(page.locator("#panel-library")).to_be_visible()
    assert page.evaluate("localStorage.getItem('harmonist-tab')") == "library"
    page.goto(ui_server + "/?tab=artists&page=1&limit=1&q=alice")
    pw.expect(page).to_have_url(re.compile(r"[?&]tab=library(?:&|$)"))
    assert parse_qs(urlsplit(page.url).query)["q"] == ["alice"]
    origin = page.url
    with page.expect_navigation():
        page.locator("#panel-library .library-artist-link").first.click()
    pw.expect(page.get_by_role("heading", name="Álice", exact=True)).to_be_visible()
    with page.expect_navigation():
        page.get_by_role("link", name="← Back to library", exact=True).click()
    pw.expect(page).to_have_url(origin)
    page.goto(ui_server + "/settings")
    checkbox = page.get_by_role("checkbox", name="Show Artists tab", exact=True)
    pw.expect(checkbox).not_to_be_checked()
    checkbox.focus()
    checkbox.press("Space")
    with page.expect_navigation():
        page.get_by_role("button", name="Save settings", exact=True).click()
    pw.expect(checkbox).to_be_checked()
    page.goto(ui_server + "/?tab=artists")
    pw.expect(artists).to_be_visible()
    pw.expect(page.locator("#panel-artists")).to_be_visible()
    page.goto(ui_server + "/settings")
    checkbox.uncheck()
    with page.expect_navigation():
        page.get_by_role("button", name="Save settings", exact=True).click()
    pw.expect(checkbox).not_to_be_checked()
    # Return to the home document from before the preference changed.
    for _ in range(3):
        page.go_back()
        if urlsplit(page.url).path == "/":
            break
    pw.expect(artists).to_have_count(0)
    pw.expect(page.locator("#panel-library")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    # The existing live status poll also repairs an out-of-date restored body.
    with page.expect_navigation():
        page.evaluate("""() => {
            document.querySelector('[data-artists-tab]').dataset.artistsTab = 'true';
        }""")
    pw.expect(artists).to_have_count(0)
    pw.expect(page.locator("#panel-library")).to_be_visible()
    page.context.close()
