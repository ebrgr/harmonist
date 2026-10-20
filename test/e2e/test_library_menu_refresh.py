"""A background refresh keeps an open Library filter menu open (#823)."""

import os
import re

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("RUN_E2E") != "1", reason="e2e disabled")
pw = pytest.importorskip("playwright.sync_api")


@pytest.mark.parametrize(("engine", "width"), [("chromium", 1280), ("webkit", 390)])
# Artwork's options carry ids; Origin's are plain links, found again by href.
@pytest.mark.parametrize(("key", "choice"), [("artwork", "No artwork"), ("origin", "Unknown")])
def test_background_refresh_keeps_open_menu(
    browser_factory, library_filters_server, htmx_ready, engine, width, key, choice
):
    browser = browser_factory(engine)
    page = browser.new_page(viewport={"width": width, "height": 900})
    page.goto(f"{library_filters_server}/?tab=library")
    trigger = page.locator(f"#library-{key}-button")
    menu = page.locator(f"#library-{key}-menu")
    option = menu.get_by_role("link", name=re.compile(f"^{choice}"))
    htmx_ready(trigger).wait_for(state="visible")

    # A scan finishing, say, sends the refresh before the menu opens and its
    # response lands while it is open: hold the response across the opening.
    held = []
    page.route(re.compile(r"/library\?page="), lambda route: held.append(route), times=1)
    page.evaluate("document.getElementById('library-page').dataset.stale = '1'")
    with page.expect_request(re.compile(r"/library\?page=")):
        page.evaluate("document.body.dispatchEvent(new Event('library-refresh'))")
    trigger.click()
    pw.expect(menu).to_be_visible()
    option.focus()
    assert len(held) == 1
    held.pop().continue_()
    pw.expect(page.locator("#library-page[data-stale]")).to_have_count(0)
    page.wait_for_function("() => !document.querySelector('.htmx-request, .htmx-settling')")

    pw.expect(menu).to_be_visible()
    pw.expect(option).to_be_focused()
    # A choice made in the menu still closes it.
    htmx_ready(option).click()
    pw.expect(trigger).to_contain_text(choice)
    pw.expect(menu).to_be_hidden()
    page.context.close()
