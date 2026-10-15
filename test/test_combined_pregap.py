"""A combined rip is an explicit album decision, never a timing-based assignment."""

import json
from dataclasses import replace

import pytest
from bs4 import BeautifulSoup

from harmonist import (
    activity_store,
    formats,
    gardener,
    pregaps,
    scanner,
    sidecar,
    tagger,
    track_assignment,
)
from harmonist.models import AlbumState, IncludedPregap, Sidecar
from harmonist.transforms import TaggingChoices
from test import test_web
from test.helpers import write_track_totals
from test.test_pregap import pregap_release
from test.test_pregap_web import confirmed_album

cfg = test_web.cfg
client = test_web.client


@pytest.fixture
def combined_album(cfg, monkeypatch):
    root, release, calls = confirmed_album(cfg, monkeypatch)
    write_track_totals(root, track_total=3)
    medium = release["medium-list"][0]
    medium["pregap"] = pregap_release()["medium-list"][0]["pregap"]
    medium["pregap"]["length"] = "10000"
    medium["track-list"][0]["length"] = "20000"
    read_tags, read_scan = formats.read_tags, formats.read_scan_fields

    def tags(path):
        tag = read_tags(path)
        return replace(tag, duration_ms=30000) if tag.track_num == 1 else tag

    def scan(path):
        fields = read_scan(path)
        return (
            fields._replace(duration_ms=30000)
            if fields.release_track_id == medium["track-list"][0]["id"]
            else fields
        )

    monkeypatch.setattr(formats, "read_tags", tags)
    # Installed after the first red rendering check, which needs no scan duration.
    return root, release, calls, scan


def test_editor_offers_combined_pregap_with_timing_evidence(client, cfg, combined_album):
    root, _, _, _ = combined_album
    aid = test_web._id_for(cfg, root)
    response = client.get(f"/assignments/{aid}?album_tracks=true&on_album_page=true")
    soup = BeautifulSoup(response.text, "html.parser")
    box = soup.select_one('input[name="included_pregaps"]')
    assert box is not None
    assert box["value"] == "rt-pregap"
    assert "checked" not in box.attrs
    assert "Track 1 includes the pregap" in soup.get_text()
    assert "0:10 + 0:20" in soup.get_text()
    assert "0:30" in soup.get_text()
    assert "included_pregaps" not in json.loads((root / ".harmonist.json").read_text())


def test_sidecar_preserves_scoped_combined_pregap_decision(tmp_path):
    raw = {
        "schema_version": 1,
        "mb_release_id": "rel-1",
        "included_pregaps": [
            {
                "release": "rel-1",
                "pregap": "p",
                "track": "t",
                "duration_ms": 30000,
                "track_total": 3,
            }
        ],
    }
    path = tmp_path / ".harmonist.json"
    path.write_text(json.dumps(raw))
    stored = sidecar.read(tmp_path)
    assert stored is not None
    sidecar.write(tmp_path, stored)
    assert json.loads(path.read_text())["included_pregaps"] == raw["included_pregaps"]


def test_accept_and_reverse_only_changes_completeness(client, cfg, combined_album, monkeypatch):
    root, _, calls, scan = combined_album
    monkeypatch.setattr(formats, "read_scan_fields", scan)
    aid = test_web._id_for(cfg, root)
    files = sorted(root.glob("*.m4a"))
    before = [f.read_bytes() for f in files]
    editor = client.get(f"/assignments/{aid}?album_tracks=true&on_album_page=true")
    data = test_web._confirmation_fields(editor.text) | {"included_pregaps": "rt-pregap"}
    assert client.post(f"/confirm/{aid}/accept", data=data).status_code == 200
    assert scanner.scan(cfg.paths.music_dir)[0].state == AlbumState.COMPLETE
    assert scanner.scan(cfg.paths.music_dir)[0].expected_track_count == 3
    assert [f.read_bytes() for f in files] == before
    assert all(formats.read_tags(f).owned["track_total"] == 3 for f in files)
    assert any("included_pregaps" in e.message for e in activity_store.recent(100))
    comparison = client.get(f"/library/{aid}/compare")
    assert "Included in track 1" in comparison.text
    assert "Not in your files" not in comparison.text
    assert not gardener.plan_for(scanner.scan(cfg.paths.music_dir)[0], combined_album[1]).changes
    # Reopening and accepting again is a no-op for the persisted decision.
    editor = client.get(f"/assignments/{aid}?album_tracks=true&on_album_page=true")
    assert 'name="included_pregaps"' in editor.text
    saved = (root / ".harmonist.json").read_text()
    data = test_web._confirmation_fields(editor.text) | {"included_pregaps": "rt-pregap"}
    client.post(f"/confirm/{aid}/accept", data=data)
    assert (root / ".harmonist.json").read_text() == saved
    editor = client.get(f"/assignments/{aid}?album_tracks=true&on_album_page=true")
    data = test_web._confirmation_fields(editor.text)
    data.pop("included_pregaps", None)
    client.post(f"/confirm/{aid}/accept", data=data)
    assert scanner.scan(cfg.paths.music_dir)[0].state == AlbumState.INCOMPLETE
    assert "included_pregaps" not in json.loads((root / ".harmonist.json").read_text())
    assert [c for c in calls if c[0] == "mb"] == [("mb", "rel-new-confirm")]


@pytest.mark.parametrize(
    "delta,offered", [(-2001, False), (-2000, True), (2000, True), (2001, False)]
)
def test_offer_duration_boundary(combined_album, delta, offered):
    root, release, _, _ = combined_album
    release["medium-list"][0]["pregap"]["length"] = str(10000 + delta)
    panel = track_assignment.panel(sorted(root.glob("*.m4a")), release, confirmed=True)
    assert bool(panel.available_pregaps) is offered


@pytest.mark.parametrize(
    "change",
    [
        "missing_length",
        "small_gap",
        "video",
        "first_video",
        "wrong_id",
        "wrong_release",
        "duplicate",
        "track_zero",
    ],
)
def test_no_offer_without_unambiguous_combined_rip(combined_album, change):
    root, release, _, _ = combined_album
    tags = [formats.read_tags(f) for f in sorted(root.glob("*.m4a"))]
    medium = release["medium-list"][0]
    if change == "missing_length":
        medium["pregap"].pop("length")
    elif change == "small_gap":
        medium["pregap"]["length"] = "1000"
        medium["track-list"][0]["length"] = "29000"
    elif change == "video":
        medium["pregap"]["recording"]["video"] = "true"
    elif change == "first_video":
        medium["track-list"][0]["recording"]["video"] = "true"
    elif change == "wrong_id":
        tags[0] = replace(tags[0], release_track_id="elsewhere")
    elif change == "wrong_release":
        tags[0] = replace(tags[0], owned={**tags[0].owned, "mb_album_id": "elsewhere"})
    elif change == "duplicate":
        tags.append(tags[0])
    elif change == "track_zero":
        tags.append(replace(tags[0], track_num=0))
    from harmonist import match

    assert not pregaps.offers(
        release,
        tags,
        tagger.tagsets_for(release, TaggingChoices()),
        match.mb_track_lengths(release),
    )


def test_saved_choice_credits_only_one_track_and_only_the_reviewed_rip():
    decision = IncludedPregap("release", "gap", "first", 30000, 4)
    first = formats.ScanFields(
        "Album",
        "release",
        "Artist",
        "ALAC",
        disc_num=1,
        track_total=4,
        disc_total=1,
        release_track_id="first",
        duration_ms=30000,
    )
    second = first._replace(release_track_id="second", duration_ms=5000)
    credit = pregaps.effective([first, second], [decision], "release")
    assert credit == (decision,)
    # Another ordinary track is absent: crediting the pregap cannot hide it.
    assert not scanner.expected_tracks([first, second], included_pregaps=credit).complete
    for fields, release in [
        ([first._replace(duration_ms=20000), second], "release"),
        ([first._replace(unreadable=True), second], "release"),
        ([second], "release"),
        ([first, first, second], "release"),
        ([first, second, first._replace(release_track_id="gap")], "release"),
        ([first._replace(track_total=3), second._replace(track_total=3)], "release"),
        ([first, second], "new-release"),
    ]:
        assert not pregaps.effective(fields, [decision], release)
    # The merged view carries the owner's decision once, without mutating parts.
    merged = scanner._merge_sidecars(
        [
            Sidecar(mb_release_id="release", included_pregaps=(decision,)),
            Sidecar(mb_release_id="release"),
        ],
        "release",
    )
    assert merged.included_pregaps == (decision,)


def test_old_sidecar_and_invalid_combined_preferences(tmp_path):
    sidecar.write(tmp_path, Sidecar(mb_release_id="release"))
    assert sidecar.read(tmp_path).included_pregaps == ()
    assert "included_pregaps" not in json.loads((tmp_path / ".harmonist.json").read_text())
    for invalid in [
        True,
        [{}],
        [{"release": "r", "pregap": "p", "track": "t", "duration_ms": -1, "track_total": 3}],
    ]:
        (tmp_path / ".harmonist.json").write_text(
            json.dumps({"schema_version": 1, "mb_release_id": "r", "included_pregaps": invalid})
        )
        with pytest.raises(sidecar.InvalidSidecarError):
            sidecar.read(tmp_path)


def test_draft_moves_preserve_choice_but_incompatible_apply_is_rejected(
    client, cfg, combined_album
):
    root, _, _, _ = combined_album
    aid = test_web._id_for(cfg, root)
    editor = client.get(f"/assignments/{aid}?album_tracks=true&on_album_page=true")
    data = test_web._confirmation_fields(editor.text) | {
        "included_pregaps": "rt-pregap",
        "move": "disk:1:down",
        "album_tracks": "true",
    }
    moved = client.post(f"/assignments/{aid}", data=data)
    soup = BeautifulSoup(moved.text, "html.parser")
    assert soup.select_one('input[name="included_pregaps"]') is not None
    assert "no longer qualify" in soup.get_text()
    before = (root / ".harmonist.json").read_bytes()
    apply_data = test_web._confirmation_fields(moved.text) | {"included_pregaps": "rt-pregap"}
    refused = client.post(f"/confirm/{aid}/accept", data=apply_data)
    assert "no longer matches" in refused.text
    assert (root / ".harmonist.json").read_bytes() == before


@pytest.mark.parametrize("suffix", ["m4a", "mp3", "flac", "opus"])
def test_scan_and_comparison_read_the_same_audio_duration(suffix):
    from pathlib import Path

    file = Path(__file__).parent / "fixtures" / f"sine.{suffix}"
    duration = formats.read_scan_fields(file).duration_ms
    assert duration == formats.read_tags(file).duration_ms
    assert duration is not None and duration > 0
