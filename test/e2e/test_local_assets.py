"""Core browser interactions work without access to a third-party CDN."""

import os
from urllib.parse import urlsplit

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("RUN_E2E") != "1", reason="e2e disabled")
pw = pytest.importorskip("playwright.sync_api")


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_navigation_and_morphing_work_without_external_requests(
    browser_factory, demo_server, engine
):
    browser = browser_factory(engine)
    page = browser.new_page()
    origin = urlsplit(demo_server).netloc
    page.route(
        "**/*",
        lambda route: (
            route.continue_() if urlsplit(route.request.url).netloc == origin else route.abort()
        ),
    )
    page.goto(demo_server)
    page.evaluate("window.originalDocument = document")
    with page.expect_response(lambda r: "/activity" in r.url and r.request.method == "GET"):
        page.click('[data-tab="activity"]')
    row = page.locator("#activity-feed li").first
    pw.expect(row).to_be_visible()
    page.wait_for_function(
        "() => !document.querySelector('#activity-feed.htmx-settling, #activity-feed .htmx-added')"
    )
    assert page.evaluate("document === window.originalDocument")
    row.evaluate("el => { window.originalRow = el; }")
    page.evaluate("""() => {
            window.feedSettled = false;
            document.body.addEventListener('htmx:afterSettle', function settled(event) {
                if (event.target.id !== 'activity-feed') return;
                window.feedSettled = true;
                document.body.removeEventListener('htmx:afterSettle', settled);
            });
            // An unchanged feed normally returns 204; request a full response
            // so the identity assertion exercises the morph extension.
            document.getElementById('feed-version').dataset.version = '';
            htmx.trigger(document.body, 'refresh-feed');
        }""")
    page.wait_for_function("window.feedSettled")
    assert row.evaluate("el => el === window.originalRow")
    page.context.close()
