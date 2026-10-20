"""Dismissing a possible mismatch reveals MB contributions in place (#618)."""

import os

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("RUN_E2E") != "1", reason="e2e disabled")
playwright_sync = pytest.importorskip("playwright.sync_api")

ALBUM = "demo-rel-dingoes"


def test_dont_warn_hides_the_review_and_reveals_the_contribution(
    browser_factory, release_match_server, htmx_ready
):
    server = release_match_server
    browser = browser_factory("chromium")
    page = browser.new_page()
    discoveries = []
    page.on(
        "request",
        lambda r: discoveries.append(r.url) if r.url.endswith("/contributions/editions") else None,
    )
    page.goto(f"{server}/album/{ALBUM}")
    panel = page.locator(f"#album-contributions-{ALBUM}")
    mismatch = panel.locator("section").filter(has_text="Possible mismatch")
    reason = "Your download came from dingoes.bandcamp.com."
    box = htmx_ready(
        panel.get_by_role("checkbox", name="Don't warn unless a better match is found")
    )
    editor = panel.get_by_role("link", name="Edit store link on MusicBrainz")
    playwright_sync.expect(mismatch.get_by_text(reason)).to_be_visible()
    playwright_sync.expect(editor).to_be_hidden()
    rows = panel.get_by_role("list", name="Release candidates").get_by_role("listitem")
    playwright_sync.expect(rows.first).to_contain_text("Current match")
    playwright_sync.expect(rows.nth(1)).to_contain_text("Links dingoes.bandcamp.com")
    # "None of these": set apart at the right of the review's actions.
    add = panel.get_by_role("link", name="Add Release")
    section, action = mismatch.bounding_box(), add.bounding_box()
    assert section is not None and action is not None
    padding = mismatch.evaluate("el => parseFloat(getComputedStyle(el).paddingRight)")
    assert abs(section["x"] + section["width"] - padding - action["x"] - action["width"]) <= 2
    assert len(discoveries) == 1

    # The click alone shows the contribution: the save re-renders nothing.
    with page.expect_response(lambda r: r.url.endswith("/release-accepted")) as saved:
        box.check()
    assert saved.value.ok
    playwright_sync.expect(editor).to_be_visible()
    playwright_sync.expect(mismatch.get_by_text(reason)).to_be_hidden()
    playwright_sync.expect(box).to_be_visible()
    assert len(discoveries) == 1

    page.reload()
    playwright_sync.expect(box).to_be_checked()
    playwright_sync.expect(editor).to_be_visible()
    playwright_sync.expect(mismatch.get_by_text(reason)).to_be_hidden()

    with page.expect_response(lambda r: r.url.endswith("/release-accepted")):
        box.uncheck()
    playwright_sync.expect(mismatch.get_by_text(reason)).to_be_visible()
    playwright_sync.expect(editor).to_be_hidden()

    # A save that fails puts the box, and so the sections, back.
    page.route("**/release-accepted", lambda route: route.fulfill(status=500, body="failed"))
    with page.expect_response(lambda r: r.url.endswith("/release-accepted")):
        box.click()
    playwright_sync.expect(box).not_to_be_checked()
    playwright_sync.expect(mismatch.get_by_text(reason)).to_be_visible()
    playwright_sync.expect(editor).to_be_hidden()
    page.context.close()


def test_new_sibling_renews_warning_without_changing_match(
    browser_factory, release_match_server, htmx_ready
):
    server = release_match_server
    browser = browser_factory("chromium")
    page = browser.new_page(viewport={"width": 390, "height": 844})
    page.goto(f"{server}/album/demo-rel-wyld")
    panel = page.locator("#album-contributions-demo-rel-wyld")
    box = htmx_ready(
        panel.get_by_role("checkbox", name="Don't warn unless a better match is found")
    )
    reason = panel.get_by_text("Matched to a CD release", exact=True)
    playwright_sync.expect(reason).to_be_visible()
    with page.expect_response(lambda r: r.url.endswith("/release-accepted")) as saved:
        box.check()
    assert saved.value.ok
    playwright_sync.expect(reason).to_be_hidden()
    contribution = panel.locator("section").filter(has_text="MB contributions")
    playwright_sync.expect(contribution.get_by_role("link", name="Add Release")).to_be_visible()
    playwright_sync.expect(
        panel.get_by_role("link", name="Edit store link on MusicBrainz")
    ).to_have_count(0)
    page.reload()
    playwright_sync.expect(box).to_be_checked()
    added = page.request.post(f"{server}/test/new-digital-sibling", headers={"HX-Request": "true"})
    assert added.ok
    page.reload()
    playwright_sync.expect(box).not_to_be_checked()
    playwright_sync.expect(reason).to_be_visible()
    playwright_sync.expect(panel.get_by_text("New digital release", exact=False)).to_be_visible()
    assert page.url.endswith("/album/demo-rel-wyld")
    # The longer label wraps at a phone width without horizontal overflow.
    assert box.locator("..").evaluate("el => el.getBoundingClientRect().right") <= 390
    with page.expect_response(lambda r: r.url.endswith("/release-accepted")):
        box.check()
    page.reload()
    playwright_sync.expect(box).to_be_checked()
    page.context.close()
