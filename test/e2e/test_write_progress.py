"""Tag and artwork writes share the title overlay, action lock and live status."""

import os

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("RUN_E2E") != "1", reason="e2e disabled")
pw = pytest.importorskip("playwright.sync_api")


@pytest.mark.parametrize("kind", ["tags", "artwork"])
def test_album_write_reports_tracks_and_locks_actions(write_progress_server, kind):
    base = write_progress_server
    aid = "demo-rel-otis-day"
    with pw.sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.goto(f"{base}/album/{aid}")
        page.wait_for_selector("#album-artwork .art-row")
        page.wait_for_function("() => !document.querySelector('.htmx-request, .htmx-settling')")
        if kind == "artwork":
            page.locator("#album-artwork button.art-pick__use:visible").click()
            page.wait_for_function("() => !document.querySelector('.htmx-request, .htmx-settling')")
            button = page.get_by_role("button", name="Apply this album's artwork")
        else:
            button = page.locator(".album-tag-apply button")
        assert page.request.post(f"{base}/test/write/arm/{kind}", headers={"HX-Request": "true"}).ok
        button.click()
        bar = page.locator("#harmonist-status")
        label = "Applying artwork" if kind == "artwork" else "Tagging"
        pw.expect(bar).to_contain_text(label)
        pw.expect(bar).to_contain_text("0/2")
        pw.expect(bar).to_contain_text("01 Shout.m4a")
        pw.expect(page.locator(f"#tagging-{aid}")).to_be_visible()
        pw.expect(page.locator("button:visible:enabled")).to_have_count(0)
        assert page.request.post(f"{base}/test/write/continue", headers={"HX-Request": "true"}).ok
        pw.expect(bar).to_contain_text("1/2")
        pw.expect(bar).to_contain_text("02 Shama Lama Ding Dong.m4a")
        pw.expect(page.locator("button:visible:enabled")).to_have_count(0)
        with page.expect_navigation():
            assert page.request.post(
                f"{base}/test/write/continue", headers={"HX-Request": "true"}
            ).ok
        pw.expect(page.locator(f"#tagging-{aid}")).to_be_hidden()
        pw.expect(page.get_by_role("button", name="Forget", exact=True)).to_be_enabled()
        pw.expect(page.locator(".album-history")).to_contain_text(
            "Updated artwork on 2 files" if kind == "artwork" else "Re-tagged"
        )
        browser.close()
