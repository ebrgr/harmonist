"""Artists navigation must work in real browsers, including HTMX and Back."""

import os

import pytest
from test.e2e.conftest import _run_demo_server
from test.test_artist_web import ALICE

pytestmark = pytest.mark.skipif(os.environ.get("RUN_E2E") != "1", reason="e2e disabled")
pw = pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def artists_server(tmp_path_factory):
    yield from _run_demo_server(
        tmp_path_factory.mktemp("artists"), app_module="test.e2e.artists_app:app"
    )


@pytest.mark.parametrize("engine,width", [("chromium", 1280), ("webkit", 390)])
def test_artist_search_paging_details_and_album_return(
    browser_factory, artists_server, htmx_ready, engine, width
):
    base = artists_server
    page = browser_factory(engine).new_page(viewport={"width": width, "height": 900})
    page.goto(base + "/?tab=library")
    page.get_by_role("button", name="Artists", exact=True).click()
    panel = page.locator("#panel-artists")
    pw.expect(panel).to_be_visible()
    cards = panel.get_by_role("list", name="Artists").locator("li > a")
    heights = cards.evaluate_all("cards => cards.map(c => c.getBoundingClientRect().height)")
    assert len(heights) > 3
    assert max(heights) - min(heights) < 1
    names = panel.get_by_role("list", name="Artists").locator("li a .font-bold").all_text_contents()
    assert names[0] == "Álice"
    warning = "Unidentified credit - Another arbitrary folder"
    pw.expect(panel.get_by_text(warning, exact=True)).to_be_visible()
    thumbnail = panel.locator(f'img[src="/artist/{ALICE}/image"]')
    pw.expect(thumbnail).to_be_visible()
    page.wait_for_function(
        "src => document.querySelector(`img[src='${src}']`)?.naturalWidth > 0",
        arg=f"/artist/{ALICE}/image",
    )
    # A missing local picture leaves the reserved space and icon intact.
    missing = panel.locator('img[src="/artist/22222222-2222-4222-8222-222222222222/image"]')
    missing.scroll_into_view_if_needed()
    page.wait_for_function(
        "src => { const img = document.querySelector(`img[src='${src}']`); "
        "return img?.complete && img.style.visibility === 'hidden'; }",
        arg="/artist/22222222-2222-4222-8222-222222222222/image",
    )
    pw.expect(missing.locator("..").locator("svg")).to_be_visible()
    with page.expect_response(lambda r: "/artists?" in r.url and r.ok):
        htmx_ready(panel.get_by_role("link", name="Next →")).click()
    pw.expect(panel.locator("#artists-page")).to_have_attribute("data-page", "2")
    page.reload()
    pw.expect(panel).to_be_visible()
    pw.expect(panel.locator("#artists-page")).to_have_attribute("data-page", "2")
    search = panel.get_by_role("searchbox", name="Search artists")
    search.fill("alice")
    with page.expect_response(lambda r: "/artists?" in r.url and r.ok):
        htmx_ready(panel.get_by_role("button", name="Search", exact=True)).click()
    pw.expect(panel.get_by_role("list", name="Artists").locator("li")).to_have_count(1)
    pw.expect(panel.get_by_text(warning, exact=True)).to_be_visible()
    link = panel.get_by_role("link", name=f"Álice 2 albums {warning}", exact=True)
    link.focus()
    link.press("Enter")
    page.wait_for_url(f"**/artist/{ALICE}?**")
    pw.expect(page.get_by_role("heading", name="Álice", exact=True)).to_be_visible()
    pw.expect(page.get_by_text(warning, exact=True)).to_be_visible()
    biography = page.locator("#artist-biography")
    assert not biography.evaluate("element => element.open")
    pw.expect(biography.locator("p")).not_to_be_visible()
    biography.locator("summary").click()
    pw.expect(biography.locator("p")).to_be_visible()
    pw.expect(biography).to_contain_text("A locally stored biography.")
    biography.locator("summary").click()
    pw.expect(biography.locator("p")).not_to_be_visible()
    discography = page.locator("#artist-discography")
    assert not discography.evaluate("element => element.open")
    discography.locator("summary").click()
    pw.expect(discography.get_by_text("NFO discography album", exact=True)).to_be_visible()
    pw.expect(page.get_by_role("list", name="Artist genres")).to_contain_text("Rock")
    pw.expect(page.locator("#library-grid .lib-tile")).to_have_count(2)
    image = page.get_by_role("img", name="Álice", exact=True)
    pw.expect(image).to_be_visible()
    page.wait_for_function("document.querySelector('img[alt=\"Álice\"]').naturalWidth > 0")
    page.locator("#library-grid .lib-tile").first.click()
    page.get_by_role("link", name="← Back to artist", exact=True).click()
    pw.expect(page.get_by_role("heading", name="Álice", exact=True)).to_be_visible()
    page.get_by_role("link", name="← Back to artists", exact=True).click()
    pw.expect(panel).to_be_visible()
    pw.expect(panel.get_by_role("searchbox", name="Search artists")).to_have_value("alice")
    pw.expect(panel.get_by_role("list", name="Artists").locator("li")).to_have_count(1)
    # The browser's history path must also retain the artist and query.
    page.go_back()
    pw.expect(page.get_by_role("heading", name="Álice", exact=True)).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.context.close()


@pytest.mark.parametrize("engine,width", [("chromium", 1280), ("webkit", 390)])
def test_artist_nfo_editor_saves_tags_and_creates_a_missing_nfo(
    browser_factory, artists_server, engine, width
):
    page = browser_factory(engine).new_page(viewport={"width": width, "height": 900})
    page.goto(artists_server + f"/artist/{ALICE}")
    page.get_by_role("button", name="Edit", exact=True).click()
    dialog = page.get_by_role("dialog", name="Edit artist NFO")
    pw.expect(dialog).to_be_visible()
    original_bio = dialog.get_by_label("Biography").input_value()
    dialog.get_by_label("Biography").fill("Cancelled draft")
    dialog.get_by_role("button", name="Cancel", exact=True).click()
    pw.expect(dialog).not_to_be_visible()
    page.get_by_role("button", name="Edit", exact=True).click()
    pw.expect(dialog.get_by_label("Biography")).to_have_value(original_bio)
    genre = "Browser genre " + engine
    temporary_genre = "Temporary genre " + engine
    dialog.get_by_role("textbox", name="New genre", exact=True).fill(temporary_genre)
    dialog.get_by_role("button", name="Add genre", exact=True).click()
    temporary = dialog.get_by_role("button", name="Remove genre " + temporary_genre, exact=True)
    pw.expect(temporary).to_be_visible()
    temporary.click()
    pw.expect(temporary).to_have_count(0)
    dialog.get_by_role("textbox", name="New genre", exact=True).fill(genre)
    dialog.get_by_role("textbox", name="New genre", exact=True).press("Enter")
    pw.expect(
        dialog.get_by_role("button", name="Remove genre " + genre, exact=True)
    ).to_be_visible()
    dialog.get_by_label("Biography").fill("Edited biography from " + engine)
    with page.expect_navigation():
        dialog.get_by_role("button", name="Save NFO", exact=True).click()
    pw.expect(page.get_by_role("list", name="Artist genres")).to_contain_text(genre)
    page.locator("#artist-biography summary").click()
    pw.expect(page.locator("#artist-biography p")).to_have_text("Edited biography from " + engine)
    page.locator("#artist-discography summary").click()
    pw.expect(page.get_by_text("NFO discography album", exact=True)).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    # The name without a slash in the URL is intentionally unrelated to the
    # information folder's name: creation is scoped by the embedded artist ID.
    bob = "22222222-2222-4222-8222-222222222222"
    page.goto(artists_server + f"/artist/{bob}")
    page.get_by_role("button", name="Edit", exact=True).click()
    pw.expect(dialog).to_be_visible()
    dialog.get_by_role("textbox", name="New genre", exact=True).fill("Created " + engine)
    dialog.get_by_role("button", name="Add genre", exact=True).click()
    with page.expect_navigation():
        dialog.get_by_role("button", name="Save NFO", exact=True).click()
    pw.expect(page.get_by_role("list", name="Artist genres")).to_contain_text("Created " + engine)
    page.reload()
    pw.expect(page.get_by_role("list", name="Artist genres")).to_contain_text("Created " + engine)
    page.context.close()
