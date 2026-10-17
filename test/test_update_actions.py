"""An album finding offers only actions that can change its files."""

import shutil

import pytest
from bs4 import BeautifulSoup

from harmonist import album_files, formats, tagger
from test import test_web

cfg = test_web.cfg
client = test_web.client


@pytest.mark.parametrize("update", ["none", "tags", "artwork"])
def test_extra_video_does_not_offer_an_empty_tag_update(client, cfg, monkeypatch, update):
    release = test_web._release_for_match("rel-extra-video", n_tracks=2)
    cover = test_web._png(1)
    folder = test_web._release_backed_album_with_art(
        cfg, "ExtraVideo", release["id"], covers=[cover, None if update == "artwork" else cover]
    )
    tagger.tag_album(folder, release)
    if update == "tags":
        track = album_files.audio_files(folder)[0]
        formats.write_owned(track, dict(formats.read_owned(track), title="Old title"))
    video = folder / "Bonus.m4v"
    shutil.copy(test_web.SINE_M4A, video)
    monkeypatch.setattr("harmonist.mb_lookup.fetch_release", lambda mbid: release)
    monkeypatch.setattr("harmonist.cover_art.front_image", lambda *a, **kw: None)
    before = {p: p.read_bytes() for p in [*album_files.audio_files(folder), video]}
    aid = test_web._id_for(cfg, folder)

    response = client.get(f"/library/{aid}/compare")
    assert response.status_code == 200
    soup = BeautifulSoup(response.text, "html.parser")
    finding = soup.select_one(f"#album-update-{aid}")
    assert "1 not in MusicBrainz" in finding.get_text()
    assert bool(finding.select_one(f'form[hx-post="/retag/{aid}"]')) is (update == "tags")
    if update == "artwork":
        assert soup.select_one(f'form[hx-post="/album/{aid}/artwork/update"]')
    assert soup.select_one('[aria-label="Video file"]')
    assert {p: p.read_bytes() for p in before} == before
