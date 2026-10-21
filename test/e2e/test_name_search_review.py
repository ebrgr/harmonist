"""Search choices require review even when their tracks fit exactly (#848)."""

import os

import pytest
from test.e2e.conftest import _run_demo_server

from harmonist import formats, sidecar

pytestmark = pytest.mark.skipif(os.environ.get("RUN_E2E") != "1", reason="e2e disabled")
pw = pytest.importorskip("playwright.sync_api")


@pytest.fixture
def name_search_server(tmp_path):
    # The barcode fixture also supplies an untagged, exact-fit name-search result.
    for base in _run_demo_server(tmp_path, app_module="test.e2e.barcode_app:app"):
        yield base, tmp_path / "harmonist-demo" / "LFO" / "Frequencies"


@pytest.mark.parametrize("on_album_page,width", [(True, 1280), (False, 390)])
def test_exact_name_search_reviews_before_one_confirmation(
    browser_factory, name_search_server, htmx_ready, on_album_page, width
):
    base, folder = name_search_server
    audio = folder / "01 LFO.m4a"
    original = audio.read_bytes()
    aid = "barcode-demo"
    mbid = "b1a3a871-16c2-4e8b-b775-22785decf471"
    browser = browser_factory("chromium")
    page = browser.new_page(viewport={"width": width, "height": 900})
    dialogs = []

    def handle_dialog(dialog):
        dialogs.append(dialog.message)
        dialog.accept()

    page.on("dialog", handle_dialog)
    with page.expect_response(lambda r: r.url.endswith(f"/manual/{aid}/barcode")):
        page.goto(f"{base}/album/{aid}?from_q=LFO" if on_album_page else f"{base}/?tab=inbox")
    panel = page.locator("#album-inbox-actions" if on_album_page else f"#task-{aid}")
    htmx_ready(panel.get_by_role("radio", name="By name", exact=True)).check()
    with page.expect_response(lambda r: r.url.endswith(f"/manual/{aid}/search")):
        htmx_ready(panel.get_by_role("button", name="Search", exact=True)).click()
    results = panel.get_by_role("list", name="Release candidates")
    choice = results.get_by_role("listitem").filter(
        has=page.locator(f'form input[name="mbid"][value="{mbid}"]')
    )
    pw.expect(choice).to_contain_text("1 tracks")
    use = choice.get_by_role("button", name="Use", exact=True)

    # A failed selection retains the usable choice and leaves the files alone.
    assign_url = f"{base}/manual/{aid}/assign"
    page.route(assign_url, lambda route: route.fulfill(status=503, body="Temporarily unavailable"))
    with page.expect_response(lambda r: r.url == assign_url and r.status == 503):
        htmx_ready(use).click()
    pw.expect(use).to_be_enabled()
    assert audio.read_bytes() == original
    assert sidecar.read(folder).mb_match_candidate is None
    page.unroute(assign_url)

    with page.expect_response(lambda r: r.url == assign_url and r.ok):
        htmx_ready(use).click()
    confirm = panel.get_by_role("button", name="Confirm suggestion", exact=True)
    pw.expect(confirm).to_be_visible()
    pw.expect(panel.get_by_role("heading", name="Suggested match:")).to_be_visible()
    assert audio.read_bytes() == original
    assert sidecar.read(folder).mb_release_id is None
    assert sidecar.read(folder).mb_match_candidate.mb_release_id == mbid

    with page.expect_response(lambda r: r.url.endswith(f"/confirm/{aid}/accept") and r.ok):
        htmx_ready(confirm).click()
    if on_album_page:
        page.wait_for_url(f"{base}/album/{mbid}?from_q=LFO")
        pw.expect(page.locator("#album-identity")).to_contain_text("Frequencies")
    else:
        pw.expect(panel).to_have_count(0)
    assert formats.read_album_id(audio) == mbid
    assert sidecar.read(folder).mb_match_candidate is None
    assert dialogs == []
    page.context.close()
