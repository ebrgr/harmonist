"""Track display is a browser preference, including across comparison swaps."""

import os

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("RUN_E2E") != "1", reason="e2e disabled")
pw = pytest.importorskip("playwright.sync_api")


@pytest.mark.parametrize(
    ("album_id", "filenames"),
    [
        (
            "demo-rel-dingoes",
            ["01 Pavlov's Bell.m4a", "02 Hellmouth Lullaby.m4a", "03 Cordelia's Theme.m4a"],
        ),
        (
            "demo-rel-electric-mayhem",
            [
                "01 Can You Picture That?.flac",
                "02 Mahna Mahna.flac",
                "01 Movin' Right Along.flac",
                "02 Rainbow Connection.flac",
            ],
        ),
    ],
)
def test_track_display_survives_refresh_and_edit_cancel(
    public_demo_server, album_id, filenames, htmx_ready
):
    base, _ = public_demo_server
    with pw.sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.goto(f"{base}/album/{album_id}")
        view = page.locator("#album-track-view")
        filename = view.get_by_role("radio", name="Filename", exact=True)
        title = view.get_by_role("radio", name="Title", exact=True)
        names = view.locator("td .track-filename")
        titles = view.locator("td .track-title")
        pw.expect(title).to_be_checked()
        pw.expect(titles.first).to_be_visible()
        pw.expect(names.first).not_to_be_visible()
        filename.press("Space")
        pw.expect(filename).to_be_checked()
        pw.expect(names).to_have_text(filenames)
        for name in names.all():
            pw.expect(name).to_be_visible()
        pw.expect(titles.first).not_to_be_visible()

        with page.expect_response(lambda r: "/compare" in r.url):
            page.get_by_role(
                "button", name="Read this release from MusicBrainz again", exact=True
            ).click()
        pw.expect(filename).to_be_checked()
        pw.expect(names.first).to_be_visible()
        htmx_ready(page.get_by_role("button", name="Edit track assignments", exact=True)).click()
        editor = page.locator("#album-track-editor")
        pw.expect(editor.get_by_role("radio", name="Title", exact=True)).to_be_checked()
        editor.get_by_role("button", name="Cancel", exact=True).click()
        pw.expect(filename).to_be_checked()
        pw.expect(names.first).to_be_visible()
        title.press("Space")
        pw.expect(titles.first).to_be_visible()
        pw.expect(names.first).not_to_be_visible()
        browser.close()
