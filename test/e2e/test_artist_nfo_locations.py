"""Destination radios really control one write in desktop and mobile browsers."""

import os

import pytest
from test.e2e.conftest import _run_demo_server
from test.test_artist_nfo_location_web import CAROL

pytestmark = pytest.mark.skipif(os.environ.get("RUN_E2E") != "1", reason="e2e disabled")
pw = pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module", params=[True, False])
def location_server(tmp_path_factory, request):
    with_info = request.param
    selected = "app" if with_info else "without_info"
    for base in _run_demo_server(
        tmp_path_factory.mktemp("nfo-locations"),
        app_module=f"test.e2e.nfo_locations_app:{selected}",
    ):
        yield base, with_info


@pytest.mark.parametrize("engine,width", [("chromium", 1280), ("webkit", 390)])
def test_choose_one_destination_and_keep_existing_nfo_in_place(
    browser_factory, location_server, engine, width
):
    base, with_info = location_server
    artist_id = CAROL if engine == "chromium" else "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    name = "Carol" if engine == "chromium" else "Dave"
    new_artist_id = (
        "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
        if engine == "chromium"
        else "ffffffff-ffff-4fff-8fff-ffffffffffff"
    )
    page = browser_factory(engine).new_page(viewport={"width": width, "height": 900})
    page.goto(base + "/settings")
    page.get_by_label("Preferred location for new artist.nfo files").select_option("info_preferred")
    with page.expect_navigation():
        page.get_by_role("button", name="Save settings", exact=True).click()
    pw.expect(page.get_by_role("status").filter(has_text="Settings saved")).to_be_visible()
    page.goto(base + f"/artist/{artist_id}")
    page.get_by_role("button", name="Edit", exact=True).click()
    dialog = page.get_by_role("dialog", name="Edit artist NFO")
    pw.expect(dialog).to_be_visible()
    audio = dialog.get_by_role("radio", name="Audio folders")
    pw.expect(audio).to_be_visible()
    pw.expect(dialog.get_by_role("radio")).to_have_count(2 if with_info else 1)
    audio.check()
    dialog.get_by_label("Biography", exact=True).fill("Offline artist profile from " + engine)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    with page.expect_navigation():
        dialog.get_by_role("button", name="Save NFO", exact=True).click()
    # A second source would produce an ambiguity; the exact source path is evidence.
    pw.expect(
        page.get_by_text("Multiple artist.nfo files identify this artist.", exact=False)
    ).to_have_count(0)
    page.get_by_role("button", name="Edit", exact=True).click()
    pw.expect(dialog.get_by_role("radio", name="Existing NFO")).to_have_count(1)
    pw.expect(dialog.get_by_role("group", name="Save location")).to_contain_text(
        f"{name}/artist.nfo"
    )
    dialog.get_by_role("button", name="Cancel", exact=True).click()
    page.goto(base + "/settings")
    page.get_by_label("Preferred location for new artist.nfo files").select_option(
        "audio_preferred"
    )
    with page.expect_navigation():
        page.get_by_role("button", name="Save settings", exact=True).click()
    page.goto(base + f"/artist/{new_artist_id}")
    page.get_by_role("button", name="Edit", exact=True).click()
    pw.expect(dialog.get_by_role("radio", name="Audio folders")).to_be_checked()
    dialog.get_by_role("button", name="Cancel", exact=True).click()
    page.goto(base + f"/artist/{artist_id}")
    page.get_by_role("button", name="Edit", exact=True).click()
    pw.expect(dialog.get_by_role("radio", name="Existing NFO")).to_have_count(1)
    pw.expect(dialog.get_by_role("radio")).to_have_count(1)
    page.context.close()
