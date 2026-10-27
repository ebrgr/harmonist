"""Field-first aggregation of stored tag-change records (#86).

Pure functions over values, so these tests build `TagChanges` directly rather
than tagging real files — what's under test is the inversion from one-row-per-
file to one-row-per-field, not the recording.
"""

from __future__ import annotations

from harmonist.activity_store import TagChanges
from harmonist.formats.owned import ARTWORK, Scope
from harmonist.tag_history import display, summarise


def _album(n: int, changes_for) -> list[TagChanges]:
    """`n` files, each with whatever `changes_for(i)` returns (1-based)."""
    return [
        TagChanges(file=f"{i:02d} track.flac", changes=changes_for(i), position=str(i))
        for i in range(1, n + 1)
    ]


def _row(rows, field):
    return next(r for r in rows if r.field == field)


def test_a_change_on_every_track_becomes_one_row():
    """The point of the whole module: 18 files that all changed the same way
    are one line, not 18. A layout whose height scales with the tracklist is
    unusable exactly when it's doing the most work (#32's nightly runs)."""
    records = _album(18, lambda i: {"artist": ["Boards Of Canada", "Boards of Canada"]})

    rows = summarise(records)

    assert len(rows) == 1
    assert rows[0].before == "Boards Of Canada"
    assert rows[0].after == "Boards of Canada"
    assert rows[0].tracks == 18
    assert rows[0].uniform


def test_reach_distinguishes_album_wide_from_partial_and_per_track():
    """The annotation beside each value, and the reason the per-album/per-track
    split from #149 is visible rather than plumbing."""
    records = _album(
        18,
        lambda i: (
            {"label": [None, "Warp Records"], "artist": ["A", "B"]}
            | ({"title": ["ROYGBIV", "Roygbiv"]} if i == 9 else {})
        ),
    )

    rows = summarise(records)

    # An album-scoped field that moved everywhere is ONE change — saying "all 18
    # tracks" would invite the reader to go and check 18 things.
    assert _row(rows, "label").reach == "album"
    assert _row(rows, "label").scope is Scope.ALBUM
    # A track-scoped field that moved everywhere did move on every track.
    assert _row(rows, "artist").reach == "all tracks"
    assert _row(rows, "artist").scope is Scope.TRACK
    # Partial always says which part, whatever the scope: this is the case where
    # the album has no single answer. One track is named rather than counted.
    assert _row(rows, "title").reach == "track 9"


def test_a_folder_cover_in_a_tagging_is_named_not_counted():
    """A tagging that creates the folder cover records it as a file of its own
    (#469). It is not a track: counting it reported a field that reached every
    one of thirteen tracks as "13 of 14"."""
    records = _album(13, lambda i: {"artist": ["A", "B"], ARTWORK: [None, "sha-new"]}) + [
        TagChanges(file="cover.jpg", changes={ARTWORK: [None, "sha-new"]})
    ]

    rows = summarise(records)

    assert _row(rows, "artist").reach == "all tracks"
    assert _row(rows, ARTWORK).reach == "all tracks and cover.jpg"


def test_an_addition_is_in_the_artwork_undo_plan():
    """Undone back to absence (#471), beside a replacement put back — and a
    record that touched no artwork is not in it."""
    from harmonist.tag_history import ArtworkRevert, artwork_revert_plan

    records = [
        TagChanges(file="01.flac", changes={ARTWORK: [None, "sha-new"]}),
        TagChanges(file="02.flac", changes={ARTWORK: ["sha-old", "sha-new"]}),
        TagChanges(file="03.flac", changes={"artist": ["A", "B"]}),
    ]

    assert artwork_revert_plan(records) == (
        ArtworkRevert(file="01.flac", before=None, after="sha-new"),
        ArtworkRevert(file="02.flac", before="sha-old", after="sha-new"),
    )


def test_an_artwork_change_to_the_folder_cover_alone_names_it():
    """The album's own image promoted over `cover.jpg`, and no track moved:
    "1 track" named something that did not happen."""
    records = [TagChanges(file="cover.jpg", changes={ARTWORK: ["sha-old", "sha-new"]})]

    assert summarise(records)[0].reach == "cover.jpg"


def test_rows_follow_the_one_change_order_with_artwork_last():
    """What a person reads before what a player files by (#273): a track title
    leads the label, identifiers come after both, and artwork sits last."""
    records = _album(
        2,
        lambda i: {
            "mb_track_id": [None, f"rec-{i}"],
            "label": [None, "L"],
            "title": ["a", "b"],
            ARTWORK: ["x", "y"],
        },
    )

    assert [r.field for r in summarise(records)] == ["title", "label", "mb_track_id", ARTWORK]


def test_a_field_that_changed_differently_per_track_keeps_every_version():
    """A compilation where each track's artist changed to something different
    has no album-wide answer."""
    records = _album(4, lambda i: {"artist": [f"old {i}", f"new {i}"]})

    row = _row(summarise(records), "artist")

    assert not row.uniform
    assert len(row.variants) == 4
    assert row.variants[0].before == "old 1"
    assert row.variants[0].position == "1"


def test_a_field_whose_files_disagree_has_no_album_pair_to_emphasise():
    """#185: 38 tracks legitimately carry 38 different recording MBIDs, and
    printing the first one beside "all tracks" reads as though the whole album
    got it. Each track's own line carries its value instead (#820)."""
    records = _album(38, lambda i: {"mb_track_id": [None, f"mbid-{i}"]})

    row = _row(summarise(records), "mb_track_id")

    assert row.reach == "all tracks"
    assert row.before_runs == () and row.after_runs == ()


def test_each_tracks_line_marks_what_changed_within_its_value():
    """#820: a compilation's mix names arriving on eight titles. Each track's
    line is read on its own, so it gets the emphasis a uniform row's pair gets:
    the added "(Patrice Bäumel mix)" is what the reader is looking for."""
    records = _album(
        2,
        lambda i: {"title": [f"Song {i}", f"Song {i} (mix {i})"]},
    )

    variant = _row(summarise(records), "title").variants[0]

    assert "".join(run.text for run in variant.after_runs) == "Song 1 (mix 1)"
    assert [run.text for run in variant.after_runs if run.changed] == [" (mix 1)"]
    assert "".join(run.text for run in variant.before_runs) == "Song 1"


def test_an_id_tracks_line_is_named_not_emphasised():
    """Two MusicBrainz ids differ in nearly every character, so emphasis on them
    is noise; the row names them instead (#801), per track as for the album."""
    records = _album(2, lambda i: {"mb_track_id": [f"old-{i}", f"new-{i}"]})

    variant = _row(summarise(records), "mb_track_id").variants[0]

    assert variant.before_runs == () and variant.after_runs == ()


def test_a_partial_change_names_the_few_tracks_it_reached():
    """#820: "Coma → COMA, 1 of 28 tracks" left the reader to find track 6. A
    change that reached a handful of tracks names them; past that the count is
    the readable form, and a change that differs per track numbers its own
    lines, so its reach stays a count."""

    def reach(changed: dict[int, list[str]], n: int = 28) -> str:
        records = _album(n, lambda i: {"artist": changed[i]} if i in changed else {})
        return _row(summarise(records), "artist").reach

    assert reach({6: ["Coma", "COMA"]}) == "track 6"
    assert reach({5: ["A", "B"], 6: ["A", "B"]}) == "tracks 5, 6"
    assert reach({i: ["A", "B"] for i in (1, 2, 3, 4)}) == "4 of 28 tracks"
    assert reach({5: ["A", "B"], 6: ["C", "D"]}) == "2 of 28 tracks"


def test_a_uniform_field_still_shows_its_value():
    """The suppression is for disagreement only. An album that moved as one has
    exactly one answer, and hiding it behind a count would make the common case
    unreadable to save the rare one."""
    row = _row(summarise(_album(18, lambda i: {"artist": ["A", "B"]})), "artist")

    assert row.uniform
    assert (row.before, row.after) == ("A", "B")


def test_an_even_split_resolves_to_the_first_files_pair():
    """The tie rule behind `_representative`, which since #185 the value cell no
    longer consults — it survives for the artwork row below. Kept the same rule
    `compare.consensus` uses on the album panel: a reader who learned it there
    must not find a different one here."""
    records = [
        TagChanges(file="01.flac", changes={"artist": ["A", "X"]}, position="1"),
        TagChanges(file="02.flac", changes={"artist": ["B", "Y"]}, position="2"),
        TagChanges(file="03.flac", changes={"artist": ["B", "Y"]}, position="3"),
        TagChanges(file="04.flac", changes={"artist": ["A", "X"]}, position="4"),
    ]

    row = _row(summarise(records), "artist")

    assert (row.before, row.after) == ("A", "X")


def test_the_representative_pair_is_a_transition_some_track_actually_made():
    """Resolving before and after independently could manufacture a change no
    file made. Here 'A' is the commonest before and 'Z' the commonest after,
    but no track went A -> Z."""
    records = [
        TagChanges(file="01.flac", changes={"artist": ["A", "X"]}, position="1"),
        TagChanges(file="02.flac", changes={"artist": ["A", "Y"]}, position="2"),
        TagChanges(file="03.flac", changes={"artist": ["B", "Z"]}, position="3"),
        TagChanges(file="04.flac", changes={"artist": ["C", "Z"]}, position="4"),
    ]

    row = _row(summarise(records), "artist")

    assert (row.before, row.after) in {("A", "X"), ("A", "Y"), ("B", "Z"), ("C", "Z")}


def test_the_artwork_row_keeps_the_image_most_of_the_album_lost():
    """Artwork is the one row that still needs a representative on a disagreeing
    album: it shows what happened rather than a value, and `before` is what
    decides whether the restore button is offered (#131). A track that never had
    an image must not talk the other three out of getting theirs back."""
    records = [
        TagChanges(file="01.flac", changes={ARTWORK: [None, "b" * 64]}, position="1"),
        *(
            TagChanges(file=f"0{i}.flac", changes={ARTWORK: ["a" * 64, "b" * 64]}, position=str(i))
            for i in (2, 3, 4)
        ),
    ]

    row = _row(summarise(records), ARTWORK)

    assert not row.uniform
    assert row.before == "a" * 64


def test_artwork_states_what_happened_instead_of_reciting_digests():
    """The digests are for #131 to find the images by, not for a person to
    read. Two sha256s would dominate every row around them and say nothing."""
    replaced = _row(summarise(_album(1, lambda i: {ARTWORK: ["a" * 64, "b" * 64]})), ARTWORK)
    added = _row(summarise(_album(1, lambda i: {ARTWORK: [None, "b" * 64]})), ARTWORK)

    assert replaced.opaque and replaced.summary == "replaced"
    assert added.opaque and added.summary == "added"
    # No in-value emphasis either: two digests differ nearly everywhere, so
    # marking the runs would be noise on top of noise.
    assert replaced.before_runs == ()
    # Artwork is not an owned field, so it has no album/track scope of its own.
    assert replaced.scope is None


def test_an_unknown_field_renders_under_its_own_name_rather_than_raising():
    """These records are permanent and unversioned: a row written today may name
    a field a later build has renamed or dropped. One old row must not take a
    whole album page down."""
    records = [TagChanges(file="01.flac", changes={"some_future_field": ["old", "new"]})]

    row = summarise(records)[0]

    assert row.label == "some_future_field"
    assert (row.before, row.after) == ("old", "new")
    assert row.scope is None


def test_a_malformed_entry_is_skipped_and_its_neighbours_survive():
    records = [TagChanges(file="01.flac", changes={"artist": "not-a-pair", "title": ["a", "b"]})]

    rows = summarise(records)

    assert [r.field for r in rows] == ["title"]


def test_absent_values_collapse_the_way_the_tagger_collapses_them():
    """None, "" and [] all mean "not there" — the record keeps them raw so a
    revert restores exactly what was there, but the reader must not draw a
    distinction the tagger doesn't make."""
    assert display(None) is None
    assert display("") is None
    assert display([]) is None
    assert display([""]) is None  # #896: a tag holding one empty value
    assert display(["a", "b"]) == "a; b"
    assert display(9) == "9"


def test_nothing_recorded_summarises_to_nothing():
    assert summarise([]) == ()
