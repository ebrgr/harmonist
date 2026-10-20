"""Review focus must not pull a visible heading behind the sticky navigation."""

import os

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("RUN_E2E") != "1", reason="e2e disabled")
pw = pytest.importorskip("playwright.sync_api")


@pytest.mark.parametrize("digital_contribution_server", ["linked"], indirect=True)
@pytest.mark.parametrize("engine", ["chromium", "webkit"])
@pytest.mark.parametrize("width", [1400, 390])
@pytest.mark.parametrize("position", ["visible", "above", "initial"])
def test_mismatch_review_keeps_its_start_visible(
    browser_factory, digital_contribution_server, htmx_ready, engine, width, position
):
    server, _ = digital_contribution_server
    browser = browser_factory(engine)
    page = browser.new_page(viewport={"width": width, "height": 720})
    page.goto(f"{server}/album/demo-rel-dingoes")
    panel = page.locator("#album-contributions-demo-rel-dingoes")
    use = htmx_ready(panel.get_by_role("button", name="Use", exact=True).first)
    pw.expect(use).to_be_visible()
    page.wait_for_function("!document.querySelector('.htmx-settling')")
    header_bottom = page.locator("body > header").evaluate("e => e.getBoundingClientRect().bottom")
    # The scrolled positions leave Use visible. On a phone the picker can
    # have its heading above the viewport while its controls remain in view.
    top = header_bottom + 24 if position == "visible" else header_bottom - 80
    if position != "initial":
        panel.evaluate("(e, top) => window.scrollBy(0, e.getBoundingClientRect().top - top)", top)
    if position == "initial":
        use.scroll_into_view_if_needed()
    else:
        use.focus()
    top = panel.bounding_box()["y"]
    before = page.evaluate("window.scrollY")
    with page.expect_response(lambda r: "/assignments/" in r.url and "cancel=true" in r.url):
        if position == "initial":
            use.click()
        else:
            page.keyboard.press("Enter")
    review = page.get_by_role("region", name="Review suggested release")
    pw.expect(review.locator(".assignment-content")).to_be_visible()
    pw.expect(review).to_be_focused()
    # Include the delayed artwork swap and browser scroll anchoring.
    pw.expect(review.get_by_alt_text("Artwork for selected release", exact=True)).to_be_visible()
    page.wait_for_function("!document.querySelector('.htmx-settling, .htmx-request')")
    page.evaluate(
        "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
    )
    review_top = review.bounding_box()["y"]
    assert review_top >= header_bottom + 8
    if top >= header_bottom + 16:
        assert page.evaluate("window.scrollY") == pytest.approx(before, abs=1)
        assert review_top == pytest.approx(top, abs=1)
    else:
        assert review_top <= header_bottom + 24
    # Cancel still returns to the choices, and keyboard focus reaches the
    # review's own controls rather than remaining in the hidden picker.
    page.keyboard.press("Tab")
    assert review.evaluate("e => e.contains(document.activeElement)")
    review.get_by_role("button", name="Cancel", exact=True).click()
    pw.expect(use).to_be_visible()
    page.context.close()
