"""The album remains the place to pick, review, cancel and confirm a release."""

import os

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("RUN_E2E") != "1", reason="e2e disabled")
pw_api = pytest.importorskip("playwright.sync_api")


def _open_picker(page, server, ready):
    page.goto(f"{server}/album/demo-rel-dingoes?from_q=Dingoes")
    dialogs = []

    def dismiss(d):
        dialogs.append(d.message)
        d.dismiss()

    page.on("dialog", dismiss)
    with page.expect_response(lambda r: r.url.endswith("/rematch")) as response:
        ready(
            page.get_by_role("button", name="Wrong MusicBrainz match — pick the correct release")
        ).click()
    assert response.value.ok
    panel = page.locator("#album-inbox-actions")
    pw_api.expect(
        panel.get_by_role("region", name="Releases in this release group")
    ).to_be_visible()
    assert "/album/" in page.url and "from_q=Dingoes" in page.url
    assert dialogs == []
    return panel, dialogs, dismiss


@pytest.mark.parametrize("width", [1280, 390])
def test_sibling_cancel_and_restore_keep_album_context(release_picker_server, htmx_ready, width):
    with pw_api.sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": 900})
        panel, dialogs, _ = _open_picker(page, release_picker_server, htmx_ready)
        choices = panel.get_by_role("region", name="Releases in this release group")
        row = choices.get_by_role("listitem").filter(has_text="Alternate pressing")
        htmx_ready(row.get_by_role("button", name="Use", exact=True)).press("Enter")
        pw_api.expect(panel.get_by_role("button", name="Confirm suggestion")).to_be_visible()
        pw_api.expect(panel).to_contain_text("Alternate pressing")
        htmx_ready(panel.get_by_role("button", name="Cancel", exact=True)).click()
        pw_api.expect(choices).to_be_visible()
        page.reload()
        pw_api.expect(choices).to_contain_text("Alternate pressing")
        htmx_ready(panel.get_by_role("button", name="Revert to Previous")).click()
        page.wait_for_url("**/album/demo-rel-dingoes?from_q=Dingoes")
        pw_api.expect(page.locator("#album-identity")).to_contain_text("MB release")
        assert dialogs == []
        browser.close()


@pytest.mark.parametrize("method", ["search", "paste"])
def test_other_release_requires_shape_confirmation(release_picker_server, htmx_ready, method):
    with pw_api.sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        panel, dialogs, dismiss = _open_picker(page, release_picker_server, htmx_ready)
        if method == "search":
            panel.get_by_role("radio", name="By name", exact=True).check()
            panel.get_by_placeholder("Album", exact=True).fill("Another release")
            htmx_ready(panel.get_by_role("button", name="Search", exact=True)).click()
            results = panel.locator('[id^="mbid-results-"]')
            htmx_ready(results.get_by_role("button", name="Use", exact=True)).click()
        else:
            panel.get_by_role("radio", name="By MBID", exact=True).check()
            paste = panel.locator('form[hx-post$="/assign"]').filter(
                has=page.locator('input[name="mbid"][type="text"]')
            )
            paste.get_by_placeholder("MB release URL or 36-char MBID").fill("demo-rel-picker-other")
            htmx_ready(paste.get_by_role("button", name="Use", exact=True)).click()
        confirm = panel.get_by_role("button", name="Confirm suggestion")
        pw_api.expect(confirm).to_be_visible()
        pw_api.expect(panel).to_contain_text("Another release")
        pw_api.expect(panel).to_contain_text("On disk · 3 files")
        pw_api.expect(panel).to_contain_text("4 tracks")
        htmx_ready(confirm).click()  # dismiss first: nothing may be written
        assert len(dialogs) == 1 and "incomplete" in dialogs[0]
        pw_api.expect(confirm).to_be_visible()
        assert "demo-rel-picker-other" not in page.url
        page.remove_listener("dialog", dismiss)
        page.on("dialog", lambda d: d.accept())
        htmx_ready(confirm).click()
        page.wait_for_url("**/album/demo-rel-picker-other?from_q=Dingoes")
        pw_api.expect(page.locator("#album-identity")).to_contain_text("3 of 4")
        browser.close()


@pytest.mark.parametrize("on_album", [True, False])
def test_release_modes_show_one_result_list_even_after_a_late_search(
    release_picker_server, htmx_ready, on_album
):
    with pw_api.sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        browses = []
        page.on("request", lambda r: browses.append(r.url) if "/siblings?" in r.url else None)
        panel, _, _ = _open_picker(page, release_picker_server, htmx_ready)
        if not on_album:
            aid = page.url.split("/album/")[1].split("?")[0]
            page.goto(release_picker_server)
            panel = page.locator(f"#task-{aid}")
        siblings = panel.get_by_role("radio", name="In this release group", exact=True)
        choices = panel.get_by_role("region", name="Releases in this release group")
        pw_api.expect(siblings).to_be_checked()
        pw_api.expect(choices).to_contain_text("Alternate pressing")
        revert = panel.get_by_role("button", name="Revert to Previous")
        pw_api.expect(revert).to_be_visible()
        assert panel.evaluate("""panel =>
            panel.querySelector('button[hx-post$="/rematch/undo"]').getBoundingClientRect().top >
            panel.querySelector('[aria-label="Releases in this release group"]').getBoundingClientRect().bottom
        """)
        pw_api.expect(panel.get_by_role("radio", name="Store URL", exact=True)).to_have_count(0)
        before = len(browses)
        pending = []
        page.route("**/manual/*/search", lambda route: pending.append(route))
        panel.get_by_role("radio", name="By name", exact=True).check()
        pw_api.expect(choices).not_to_be_visible()
        pw_api.expect(revert).to_be_visible()
        panel.get_by_placeholder("Album", exact=True).fill("Another release")
        with page.expect_request(lambda r: "/manual/" in r.url and r.url.endswith("/search")):
            htmx_ready(panel.get_by_role("button", name="Search", exact=True)).click()
        siblings.focus()
        siblings.press("Space")
        pw_api.expect(choices).to_be_visible()
        assert pending
        with page.expect_response(lambda r: "/manual/" in r.url and r.url.endswith("/search")):
            pending.pop().continue_()
        results = panel.locator('[id^="mbid-results-"]')
        pw_api.expect(results).to_contain_text("Another release")
        pw_api.expect(results).not_to_be_visible()
        assert len(browses) == before
        panel.get_by_role("radio", name="By MBID", exact=True).check()
        pw_api.expect(choices).not_to_be_visible()
        paste = panel.get_by_placeholder("MB release URL or 36-char MBID")
        pw_api.expect(paste).to_be_visible()
        pw_api.expect(results).not_to_be_visible()
        pw_api.expect(revert).to_be_visible()
        assert panel.evaluate("""panel =>
            panel.querySelector('button[hx-post$="/rematch/undo"]').getBoundingClientRect().top >
            panel.querySelector('input[name="mbid"][type="text"]').getBoundingClientRect().bottom
        """)
        page.unroute("**/manual/*/search")
        panel.get_by_role("radio", name="By name", exact=True).check()
        pw_api.expect(paste).not_to_be_visible()
        htmx_ready(panel.get_by_role("button", name="Search", exact=True)).click()
        use = results.get_by_role("button", name="Use", exact=True)
        pw_api.expect(use).to_be_visible()
        pw_api.expect(panel.get_by_role("button", name="Confirm suggestion")).to_have_count(0)
        htmx_ready(use).click()
        pw_api.expect(panel.get_by_role("button", name="Confirm suggestion")).to_be_visible()
        htmx_ready(panel.get_by_role("button", name="Cancel", exact=True)).click()
        pw_api.expect(siblings).to_be_visible()
        siblings.check()
        pw_api.expect(choices).to_be_visible()
        pw_api.expect(results).not_to_be_visible()
        browser.close()


@pytest.mark.parametrize("on_album", [True, False])
def test_no_siblings_automatically_searches_by_name(
    release_picker_no_siblings_server, htmx_ready, on_album
):
    with pw_api.sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        server = release_picker_no_siblings_server
        page.goto(f"{server}/album/demo-rel-dingoes")
        htmx_ready(
            page.get_by_role("button", name="Wrong MusicBrainz match — pick the correct release")
        ).click()
        page.wait_for_url(lambda url: "demo-rel-dingoes" not in url)
        panel = page.locator("#album-inbox-actions")
        pw_api.expect(panel.get_by_role("radio", name="By name", exact=True)).to_be_checked()
        if not on_album:
            aid = page.url.split("/album/")[1]
            page.goto(server)
            panel = page.locator(f"#task-{aid}")
        pw_api.expect(panel.get_by_role("radio", name="By name", exact=True)).to_be_checked()
        results = panel.locator('[id^="mbid-results-"]')
        pw_api.expect(results.get_by_role("button", name="Use", exact=True)).to_be_visible()
        pw_api.expect(panel.get_by_role("button", name="Revert to Previous")).to_be_visible()
        pw_api.expect(
            panel.get_by_role("region", name="Releases in this release group")
        ).not_to_be_visible()
        browser.close()


def test_late_empty_group_preserves_the_chosen_mode(release_picker_no_siblings_server, htmx_ready):
    with pw_api.sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        pending = []
        searches = []
        page.route("**/manual/*/siblings?*", lambda route: pending.append(route))
        page.on("request", lambda r: searches.append(r.url) if r.url.endswith("/search") else None)
        page.goto(f"{release_picker_no_siblings_server}/album/demo-rel-dingoes")
        with page.expect_request(lambda r: "/siblings?" in r.url):
            htmx_ready(
                page.get_by_role(
                    "button", name="Wrong MusicBrainz match — pick the correct release"
                )
            ).click()
        panel = page.locator("#album-inbox-actions")
        panel.get_by_role("radio", name="By MBID", exact=True).check()
        paste = panel.get_by_placeholder("MB release URL or 36-char MBID")
        paste.fill("demo-rel-picker-other")
        with page.expect_response(lambda r: "/siblings?" in r.url):
            pending.pop().continue_()
        pw_api.expect(panel.get_by_role("button", name="Revert to Previous")).to_be_visible()
        pw_api.expect(panel.get_by_role("radio", name="By MBID", exact=True)).to_be_checked()
        pw_api.expect(paste).to_be_visible()
        pw_api.expect(paste).to_have_value("demo-rel-picker-other")
        assert searches == []
        browser.close()
