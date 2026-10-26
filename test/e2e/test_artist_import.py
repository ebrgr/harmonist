"""Real clicks must fetch, preserve a review across pages, save and undo."""

import os

import pytest
from test.e2e import test_artists
from test.test_artist_web import ALICE

pytestmark = pytest.mark.skipif(os.environ.get("RUN_E2E") != "1", reason="e2e disabled")
pw = pytest.importorskip("playwright.sync_api")
artists_server = test_artists.artists_server


@pytest.mark.parametrize("engine,width", [("chromium", 1280), ("webkit", 390)])
def test_review_language_discography_save_and_provenance_undo(
    browser_factory, artists_server, htmx_ready, engine, width
):
    page = browser_factory(engine).new_page(viewport={"width": width, "height": 900})
    page.goto(artists_server + f"/artist/{ALICE}")
    original = page.locator("#artist-biography p").first.text_content()
    with page.expect_response(lambda r: r.url.split("?")[0].endswith("/import") and r.ok):
        htmx_ready(page.get_by_role("button", name="Import metadata", exact=True)).click()
    dialog = page.get_by_role("dialog", name="Import artist metadata")
    pw.expect(dialog).to_be_visible()
    dialog.get_by_label("Biography language").select_option("pt")
    with page.expect_response(lambda r: "/import/lookup" in r.url and r.ok):
        htmx_ready(dialog.get_by_role("button", name="Fetch metadata", exact=True)).click()
    biography = dialog.get_by_role("checkbox", name="Import Biography", exact=True)
    pw.expect(biography).to_be_visible()
    biography.check()
    dialog.get_by_role("checkbox", name="Import Genres", exact=True).check()
    with page.expect_response(lambda r: "/import/discography" in r.url and r.ok):
        htmx_ready(dialog.get_by_role("button", name="Load discography", exact=True)).click()
    pw.expect(dialog.get_by_text("1–20 of 22 release groups", exact=False)).to_be_visible()
    pw.expect(dialog.get_by_role("checkbox", name="Import Biography", exact=True)).to_be_checked()
    dialog.get_by_role("checkbox", name="Discography album 01 · 2000", exact=True).check()
    with page.expect_response(lambda r: "/import/discography" in r.url and r.ok):
        htmx_ready(dialog.get_by_role("button", name="Next page", exact=True)).click()
    pw.expect(dialog.get_by_text("21–22 of 22 release groups", exact=False)).to_be_visible()
    pw.expect(
        dialog.get_by_role(
            "checkbox", name="Discography album 01 · 2000 · selected from another page"
        )
    ).to_be_checked()
    dialog.get_by_role("checkbox", name="Discography album 21 · 2020", exact=True).check()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    with page.expect_navigation():
        htmx_ready(dialog.get_by_role("button", name="Import selected", exact=True)).click()
    pw.expect(page.get_by_role("heading", name="Álice", exact=True)).to_be_visible()
    page.locator("#artist-biography summary").click()
    pw.expect(page.locator("#artist-biography p").first).to_have_text(
        "Uma biografia do artista salva localmente depois da sua revisão."
    )
    pw.expect(
        page.locator('#artist-biography a[href="https://pt.wikipedia.org/wiki/Demo_artist"]')
    ).to_be_visible()
    page.locator("#artist-discography summary").click()
    pw.expect(page.locator("#artist-discography")).to_contain_text("Discography album 01")
    pw.expect(page.locator("#artist-discography")).to_contain_text("Discography album 21")
    pw.expect(page.locator("#artist-discography")).to_contain_text("NFO discography album")
    history = page.locator(".album-history")
    page.once("dialog", lambda confirmation: confirmation.accept())
    with page.expect_navigation():
        htmx_ready(history.get_by_role("button", name="Undo this artist NFO change").first).click()
    pw.expect(page.locator("#artist-biography p").first).to_have_text(original)
    pw.expect(page.locator("#artist-discography")).to_contain_text("NFO discography album")
    pw.expect(page.locator("#artist-discography")).not_to_contain_text("Discography album 21")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.context.close()


def test_unidentified_artist_requires_search_choice_before_review(
    browser_factory, artists_server, htmx_ready
):
    page = browser_factory("chromium").new_page(viewport={"width": 1280, "height": 900})
    page.goto(artists_server + "/?tab=artists&artist_q=Unknown%20fixture%20artist")
    page.get_by_role("list", name="Artists").locator("li > a").filter(
        has_text="Unknown fixture artist"
    ).click()
    page.wait_for_url("**/artist/local-**")
    key_url = page.url.split("?")[0]
    htmx_ready(page.get_by_role("button", name="Import metadata", exact=True)).click()
    dialog = page.get_by_role("dialog", name="Import artist metadata")
    pw.expect(dialog.get_by_label("Artist name")).to_have_value("Unknown fixture artist")
    dialog.get_by_label("Biography language").select_option("it")
    with page.expect_response(lambda r: "/import/lookup" in r.url and r.ok):
        htmx_ready(dialog.get_by_role("button", name="Search artists", exact=True)).click()
    pw.expect(dialog.get_by_role("button", name="Use this artist", exact=True)).to_be_visible()
    pw.expect(dialog.get_by_role("checkbox", name="Import Biography", exact=True)).to_have_count(0)
    with page.expect_response(lambda r: "/import/lookup" in r.url and r.ok):
        htmx_ready(dialog.get_by_role("button", name="Use this artist", exact=True)).click()
    pw.expect(dialog.get_by_role("checkbox", name="Import Biography", exact=True)).to_be_visible()
    dialog.get_by_role("checkbox", name="Import Biography", exact=True).check()
    with page.expect_navigation():
        htmx_ready(dialog.get_by_role("button", name="Import selected", exact=True)).click()
    page.goto(key_url)
    pw.expect(
        page.get_by_role("heading", name="Unknown fixture artist", exact=True)
    ).to_be_visible()
    page.locator("#artist-biography summary").click()
    pw.expect(page.locator("#artist-biography p").first).to_have_text(
        "Una biografia dell’artista salvata localmente dopo la revisione."
    )
    page.context.close()
