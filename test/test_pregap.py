"""Track zero is a real release track, including when its file is absent (#242)."""

from copy import deepcopy

from harmonist import (
    activity_store,
    album_files,
    formats,
    match,
    mb_lookup,
    scanner,
    tagger,
    track_assignment,
)
from harmonist.transforms import TaggingChoices
from test.helpers import write_track_totals
from test.test_tagger import _release_2_tracks


def pregap_release():
    release = _release_2_tracks()
    medium = release["medium-list"][0]
    medium["format"] = "CD"
    medium["track-count"] = "2"
    medium["pregap"] = {
        "id": "rt-pregap",
        "position": "0",
        "number": "0",
        "length": "186000",
        "recording": {"id": "rec-pregap", "title": "Everything Is Brand New"},
    }
    return release


def test_pregap_counts_and_lengths_share_the_taggers_track_sequence():
    release = pregap_release()
    targets = tagger.tagsets_for(release, TaggingChoices())
    assert [(t.track_num, t.track_total) for t in targets] == [(0, 3), (1, 3), (2, 3)]
    assert targets[0].title == "Everything Is Brand New"
    assert match.mb_track_lengths(release)[0] == 186000
    assert len(match.mb_track_lengths(release)) == len(targets)
    assert mb_lookup.release_summary(release)["track_count"] == 3


def test_absent_pregap_does_not_reassign_correct_files_or_reduce_totals(album_with_tracks):
    root = album_with_tracks(2)
    tagger.tag_album(root, _release_2_tracks())
    write_track_totals(root, track_total=3)
    release = pregap_release()
    files = album_files.audio_files(root)
    review = tagger.assignment_review(release, [formats.read_tags(f) for f in files])
    assert not review.required
    panel = track_assignment.panel(files, release, confirmed=True)
    assert panel.mapping() == {files[0]: 1, files[1]: 2}
    assert panel.rows[0][0] is None
    assert panel.rows[0][1].number == "1.0"
    assert not panel.proposed
    tagger.tag_album(root, release, incomplete=True)
    assert [formats.read_tags(f).track_num for f in files] == [1, 2]
    assert scanner.expected_tracks([formats.read_scan_fields(f) for f in files]).total == 3
    assert not scanner.expected_tracks([formats.read_scan_fields(f) for f in files]).complete
    assert not tagger.plan_album(root, release, incomplete=True, artwork=False).changes


def test_separate_pregap_round_trips_zero_and_is_idempotent(album_with_tracks):
    root = album_with_tracks(3)
    release = pregap_release()
    files = album_files.audio_files(root)
    tagger.tag_and_artwork(
        root, release, artwork_included=False, assignment={f: i for i, f in enumerate(files)}
    )
    tags = [formats.read_tags(f) for f in files]
    assert [t.track_num for t in tags] == [0, 1, 2]
    assert [t.release_track_id for t in tags] == ["rt-pregap", "rt-001", "rt-002"]
    panel = track_assignment.panel(files, release, confirmed=True)
    assert panel.disk[0].number == panel.mb[0].number == "1.0"
    assert scanner.expected_tracks([formats.read_scan_fields(f) for f in files]).complete
    before = [(f.read_bytes(), f.stat().st_mtime_ns) for f in files]
    writes = [e.id for e in activity_store.recent(100) if e.message.startswith("tag.track")]
    assert any("track=0" in e.message for e in activity_store.recent(100))
    tagger.tag_album(root, release)
    assert [(f.read_bytes(), f.stat().st_mtime_ns) for f in files] == before
    assert [e.id for e in activity_store.recent(100) if e.message.startswith("tag.track")] == writes


def test_track_numbers_come_from_positions_not_sequence_indices():
    release = pregap_release()
    medium = release["medium-list"][0]
    medium["track-list"].reverse()
    targets = tagger.tagsets_for(release, TaggingChoices())
    assert [(t.mb_release_track_id, t.track_num) for t in targets] == [
        ("rt-pregap", 0),
        ("rt-002", 2),
        ("rt-001", 1),
    ]


def test_audio_pregap_prevents_a_medium_being_classified_as_all_video():
    release = deepcopy(pregap_release())
    for track in release["medium-list"][0]["track-list"]:
        track["recording"]["video"] = "true"
    assert mb_lookup.video_media_of(release) == ()


def test_undo_can_find_a_renamed_pregap_by_its_unique_position(album_with_tracks):
    from harmonist.tag_history import FileRevert

    root = album_with_tracks(1)
    file = album_files.audio_files(root)[0]
    target = tagger.tagsets_for(pregap_release(), TaggingChoices())[0]
    formats.write_tags(file, target, None)
    # An older history record can lack IDs; zero must still name a position.
    plan = (FileRevert("old-filename.m4a", {"title": ("Old title", target.title)}, position="0"),)
    assert tagger.revert_tags(root, plan).files == 1
    assert formats.read_tags(file).title == "Old title"
