"""An extra video stays visible without inviting a tag write that cannot fix it."""

import os
import shutil
from pathlib import Path

import pytest

from harmonist import album_files, demo, formats, tagger

pytestmark = pytest.mark.skipif(os.environ.get("RUN_E2E") != "1", reason="e2e disabled")
pw_api = pytest.importorskip("playwright.sync_api")


def test_extra_video_only_offers_apply_after_audio_tags_change(
    release_picker_server, htmx_ready, tmp_path
):
    mbid = "demo-rel-dingoes"
    track = next(
        p for p in (tmp_path / "harmonist-demo").rglob("*.m4a") if formats.read_album_id(p) == mbid
    )
    tagger.tag_album(track.parent, demo.MB_RELEASES[mbid])
    video = track.parent / "Bonus.m4v"
    shutil.copy(Path(__file__).parents[1] / "fixtures" / "sine.m4a", video)
    before_video = video.read_bytes()
    before_audio = {p: p.read_bytes() for p in album_files.audio_files(track.parent)}
    with pw_api.sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(f"{release_picker_server}/album/{mbid}")
        finding = page.locator(f"#album-update-{mbid}")
        pw_api.expect(finding).to_contain_text("1 not in MusicBrainz")
        apply = finding.get_by_role("button", name="Apply updates", exact=True)
        pw_api.expect(apply).to_have_count(0)
        pw_api.expect(page.get_by_role("img", name="Video file", exact=True)).to_be_visible()
        assert {p: p.read_bytes() for p in before_audio} == before_audio

        old = formats.read_owned(track)
        formats.write_owned(track, dict(old, title="Wrong title"))
        page.reload()
        pw_api.expect(apply).to_be_visible()
        with page.expect_response(lambda r: r.url.endswith(f"/retag/{mbid}")) as response:
            htmx_ready(apply).click()
        assert response.value.ok
        pw_api.expect(finding).to_contain_text("1 not in MusicBrainz")
        pw_api.expect(apply).to_have_count(0)
        assert formats.read_owned(track)["title"] == old["title"]
        assert video.read_bytes() == before_video
        browser.close()
