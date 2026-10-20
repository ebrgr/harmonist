"""Broader proposals and reversible exclusions through the existing picker."""

import os
import re

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("RUN_E2E") != "1", reason="e2e disabled")
pw = pytest.importorskip("playwright.sync_api")


@pytest.mark.parametrize(("engine", "width"), [("chromium", 1440), ("webkit", 390)])
@pytest.mark.parametrize("apply_action", ["Apply this album's artwork", "Apply updates"])
def test_watch_review(
    browser_factory, artwork_watch_server, htmx_ready, engine, width, apply_action
):
    base = artwork_watch_server
    browser = browser_factory(engine)
    page = browser.new_page(viewport={"width": width, "height": 1000})
    page.goto(f"{base}/album/demo-rel-barryjive")
    watch = page.get_by_role("checkbox", name="Better artwork wanted")
    reject = page.locator(".art-pick__slide:visible").get_by_role(
        "checkbox", name="Don't suggest this image"
    )
    after = page.locator(".art-row__side--after img")
    htmx_ready(watch).check()
    pw.expect(watch).to_be_checked()
    pw.expect(after).not_to_have_count(0)
    page.wait_for_function(
        """() => [...document.querySelectorAll('.art-row__side--after img')].every(i => i.complete && i.naturalWidth > 0)"""
    )
    # This is a 320px proposal replacing a 640px cover, and the ordinary
    # Replacement filter must agree with the preview.
    page.goto(f"{base}/?tab=library&filter=artwork-replacement")
    pw.expect(page.locator("#lib-demo-rel-barryjive")).to_be_visible()
    page.locator("#lib-demo-rel-barryjive").click()
    htmx_ready(reject).check()
    pw.expect(reject).to_be_checked()
    pw.expect(after).to_have_count(0)
    page.reload()
    pw.expect(reject).to_be_checked()
    pw.expect(after).to_have_count(0)
    page.goto(f"{base}/?tab=library&filter=artwork-replacement")
    pw.expect(page.locator("#lib-demo-rel-barryjive")).to_have_count(0)
    page.goto(f"{base}/album/demo-rel-barryjive")
    # Kept in the picker, and still usable explicitly despite rejection.
    htmx_ready(page.get_by_role("button", name=re.compile(r"^Use for "))).click()
    pw.expect(after).not_to_have_count(0)
    pw.expect(reject).to_be_checked()
    page.reload()
    htmx_ready(reject).uncheck()
    pw.expect(reject).not_to_be_checked()
    pw.expect(after).not_to_have_count(0)
    # The user's existing Apply actions remain the only write controls.
    htmx_ready(page.get_by_role("button", name=apply_action, exact=True)).click()
    pw.expect(after).to_have_count(0)
    pw.expect(watch).not_to_be_checked()
    page.reload()
    pw.expect(watch).not_to_be_checked()
    # A later upload does not silently re-enable the completed request.
    response = page.request.post(
        f"{base}/test/artwork-upload/586999", headers={"HX-Request": "true"}
    )
    assert response.ok
    page.reload()
    pw.expect(watch).not_to_be_checked()
    htmx_ready(watch).check()
    pw.expect(after).not_to_have_count(0)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.context.close()
