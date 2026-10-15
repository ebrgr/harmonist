"""Pregap evidence and structural-review reasons in the assembled responses."""

import pytest
from bs4 import BeautifulSoup

from harmonist import sidecar, tagger
from harmonist.models import Sidecar
from test import test_web
from test.helpers import write_track_totals
from test.test_pregap import pregap_release

cfg = test_web.cfg
client = test_web.client


def confirmed_album(cfg, monkeypatch):
    root, release, _, _, calls = test_web._confirmation_setup(cfg, monkeypatch, old_mbid=None)
    tagger.tag_album(root, release)
    sidecar.write(root, Sidecar(mb_release_id=release["id"]))
    return root, release, calls


def test_pregap_missing_file_is_visible_without_a_false_assignment_review(client, cfg, monkeypatch):
    root, release, calls = confirmed_album(cfg, monkeypatch)
    write_track_totals(root, track_total=3)
    release["medium-list"][0]["pregap"] = pregap_release()["medium-list"][0]["pregap"]
    aid = test_web._id_for(cfg, root)
    comparison = client.get(f"/library/{aid}/compare")
    soup = BeautifulSoup(comparison.text, "html.parser")
    tracks = soup.select_one("#album-track-view")
    assert "Everything Is Brand New" in tracks.get_text()
    button = tracks.select_one('[aria-controls="album-track-editor"]')
    assert "load" not in str(button.get("hx-trigger", ""))
    editor = client.get(f"/assignments/{aid}?album_tracks=true&on_album_page=true")
    rows = BeautifulSoup(editor.text, "html.parser").select("[data-assignment-row]")
    assert len(rows) == 3
    cells = rows[0].find_all("td", recursive=False)
    assert "Missing on disk" in cells[1].get_text()
    assert cells[3].get_text(strip=True) == "0"
    assert test_web._confirmation_fields(editor.text)["incomplete"] == "true"
    assert [c for c in calls if c[0] == "mb"] == [("mb", release["id"])]


@pytest.mark.parametrize("change", ["count", "numbering", "unchanged"])
def test_required_assignment_review_shows_its_derived_reason(client, cfg, monkeypatch, change):
    root, release, _ = confirmed_album(cfg, monkeypatch)
    expected = []
    if change == "count":
        write_track_totals(root, track_total=3)
        expected = ["Disc 1: track total 3 on disk → 2 on MusicBrainz."]
    elif change == "numbering":
        release["medium-list"][0]["track-list"][0]["position"] = "2"
        release["medium-list"][0]["track-list"][1]["position"] = "1"
        expected = ["Track numbering differs from MusicBrainz."]
    aid = test_web._id_for(cfg, root)
    response = client.get(f"/assignments/{aid}?album_tracks=true&on_album_page=true")
    soup = BeautifulSoup(response.text, "html.parser")
    reasons = soup.select("[data-structural-reason]")
    assert [r.get_text(strip=True) for r in reasons] == expected
    assert all(r.find_parent("details") is None for r in reasons)
