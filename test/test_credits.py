"""Composer, lyricist, writer and work credits (#680): reading them from a
release, and the tagging that writes them.

The dicts here are shaped like musicbrainzngs' parse of a real release fetched
with `RELEASE_INCLUDES`: Glenn Gould's 1982 Goldberg Variations, Coltrane's My
Favorite Things and Rumours were captured to check the shapes. The expected
values come from Picard's rules in `picard.mbjson` (`_ARTIST_REL_TYPES`,
`_relations_to_metadata_target_type_artist`, `work_to_metadata`), restated
beside each test rather than read from Harmonist's own tables.
"""

from __future__ import annotations

import copy
import shutil
from pathlib import Path
from typing import Any

import pytest

from harmonist import credits, tagger
from harmonist import formats as formats_mod
from harmonist.credits import Person, TrackCredits, Work
from harmonist.formats.owned import Significance
from harmonist.transforms import ArtistNames, TaggingChoices

from .test_tagger import _detail, _release_2_tracks

BACH: dict[str, Any] = {
    "id": "24f1766e-9635-4d58-a4d4-9413f9f98a4c",
    "name": "Johann Sebastian Bach",
    "sort-name": "Bach, Johann Sebastian",
}
RODGERS: dict[str, Any] = {
    "id": "art-rodgers",
    "name": "Richard Rodgers",
    "sort-name": "Rodgers, Richard",
}
HAMMERSTEIN: dict[str, Any] = {
    "id": "art-hammerstein",
    "name": "Oscar Hammerstein II",
    "sort-name": "Hammerstein, Oscar, II",
}
NICKS: dict[str, Any] = {"id": "art-nicks", "name": "Stevie Nicks", "sort-name": "Nicks, Stevie"}


def _rel(kind: str, artist: dict[str, Any], credited: str | None = None) -> dict[str, Any]:
    rel: dict[str, Any] = {
        "type": kind,
        "direction": "backward",
        "target": artist["id"],
        "artist": artist,
    }
    if credited is not None:
        rel["target-credit"] = credited
    return rel


def _work(wid: str, title: str, *people: dict[str, Any]) -> dict[str, Any]:
    return {"id": wid, "title": title, "artist-relation-list": list(people)}


def _performs(work: dict[str, Any], *attributes: str) -> dict[str, Any]:
    rel: dict[str, Any] = {
        "type": "performance",
        "direction": "forward",
        "target": work["id"],
        "work": work,
    }
    if attributes:
        rel["attribute-list"] = list(attributes)
    return rel


def _track(
    *performances: dict[str, Any], artists: tuple[dict[str, Any], ...] = ()
) -> dict[str, Any]:
    recording: dict[str, Any] = {"id": "rec-1", "title": "Track"}
    if performances:
        recording["work-relation-list"] = list(performances)
    if artists:
        recording["artist-relation-list"] = list(artists)
    return {"id": "trk-1", "position": "1", "recording": recording}


def _release(track: dict[str, Any], *release_rels: dict[str, Any]) -> dict[str, Any]:
    release: dict[str, Any] = {
        "id": "rel-1",
        "medium-list": [{"position": "1", "track-list": [track]}],
    }
    if release_rels:
        release["artist-relation-list"] = list(release_rels)
    return release


def _credits(track: dict[str, Any], *release_rels: dict[str, Any], **choices: Any) -> TrackCredits:
    return credits.track_credits(_release(track, *release_rels), track, TaggingChoices(**choices))


def _person(artist: dict[str, Any]) -> Person:
    return Person(artist["name"], artist["sort-name"], artist["id"])


ARIA = _work(
    "6934e59b-e82c-3050-b0cf-70907db1f1a3",
    "Goldberg-Variationen, BWV 988: Aria",
    _rel("composer", BACH),
)
MY_FAVORITE_THINGS = _work(
    "edf0bfbc-c9e4-3b7d-9765-e8b86d9febbc",
    "My Favorite Things",
    _rel("composer", RODGERS, "Richard Rodgers"),
    _rel("lyricist", HAMMERSTEIN, "Oscar Hammerstein II"),
)


def test_a_performed_works_composer_and_title_are_credited() -> None:
    assert _credits(_track(_performs(ARIA))) == TrackCredits(
        composers=(_person(BACH),),
        works=(
            Work("Goldberg-Variationen, BWV 988: Aria", "6934e59b-e82c-3050-b0cf-70907db1f1a3"),
        ),
    )


def test_a_sung_performance_credits_the_lyricist() -> None:
    found = _credits(_track(_performs(MY_FAVORITE_THINGS, "cover")))
    assert found.composers == (_person(RODGERS),)
    assert found.lyricists == (_person(HAMMERSTEIN),)


def test_an_instrumental_performance_credits_no_lyricist() -> None:
    # Picard: `if context.instrumental and name == 'lyricist': return`. The
    # composer still wrote what was played.
    found = _credits(_track(_performs(MY_FAVORITE_THINGS, "cover", "instrumental")))
    assert found.composers == (_person(RODGERS),)
    assert found.lyricists == ()


def test_an_instrumental_performance_keeps_its_writer() -> None:
    # Only the lyricist is dropped; a writer is credited for music and words
    # together, and Picard keeps it.
    song = _work("w-dreams", "Dreams", _rel("writer", NICKS))
    assert _credits(_track(_performs(song, "instrumental"))).writers == (_person(NICKS),)


def test_a_librettist_is_credited_as_a_lyricist() -> None:
    opera = _work("w-opera", "Die Zauberflöte, K. 620: Overture", _rel("librettist", HAMMERSTEIN))
    assert _credits(_track(_performs(opera))).lyricists == (_person(HAMMERSTEIN),)


def test_other_relationship_types_are_not_credits() -> None:
    # Arranger, producer and the performers are other Picard tags, outside #680.
    work = _work("w-1", "Work", _rel("arranger", RODGERS), _rel("composer", BACH))
    track = _track(
        _performs(work), artists=(_rel("producer", NICKS), _rel("instrument", HAMMERSTEIN))
    )
    assert _credits(track) == TrackCredits(composers=(_person(BACH),), works=(Work("Work", "w-1"),))


def test_a_medley_credits_every_work_and_each_composer_once() -> None:
    first = _work("w-1", "First", _rel("composer", RODGERS))
    second = _work("w-2", "Second", _rel("composer", BACH), _rel("composer", RODGERS))
    found = _credits(_track(_performs(first, "medley"), _performs(second, "medley")))
    assert found.works == (Work("First", "w-1"), Work("Second", "w-2"))
    assert found.composers == (_person(RODGERS), _person(BACH))


def test_the_releases_own_composer_comes_first() -> None:
    # Picard builds a track's metadata on top of the release's, and adds the
    # works' credits to it without repeating a value already there.
    found = _credits(_track(_performs(ARIA)), _rel("composer", RODGERS), _rel("composer", BACH))
    assert found.composers == (_person(RODGERS), _person(BACH))


def test_a_release_composer_credits_a_track_with_no_work() -> None:
    assert _credits(_track(), _rel("composer", BACH)) == TrackCredits(composers=(_person(BACH),))


def test_a_track_with_no_works_has_no_credits() -> None:
    assert _credits(_track()) == TrackCredits()


def test_two_people_sharing_a_name_stay_two_credits() -> None:
    # Where Harmonist departs from Picard: Picard keeps one "John Williams" in
    # `composer` beside two ids in `musicbrainz_composerid`, and the lists no
    # longer pair up. Removing duplicates by person keeps them aligned.
    film = {"id": "art-jw-film", "name": "John Williams", "sort-name": "Williams, John"}
    guitar = {"id": "art-jw-guitar", "name": "John Williams", "sort-name": "Williams, John"}
    work = _work("w-1", "Work", _rel("composer", film), _rel("composer", guitar))
    assert _credits(_track(_performs(work))).composers == (_person(film), _person(guitar))


@pytest.mark.parametrize(
    ("names", "expected"),
    [
        # As credited: the credited name, sorted by the artist (the XML gives a
        # relationship's artist no aliases to sort a credited name by).
        (ArtistNames.NONE, Person("J.S. Bach", "Bach, Johann Sebastian", BACH["id"])),
        # Standardized: the artist's own name. With no aliases to show the
        # credit is a former name, "variations only" standardizes it too.
        (ArtistNames.VARIATIONS, _person(BACH)),
        (ArtistNames.ALL, _person(BACH)),
    ],
)
def test_a_credited_name_follows_the_artist_name_setting(
    names: ArtistNames, expected: Person
) -> None:
    work = _work("w-1", "Work", _rel("composer", BACH, "J.S. Bach"))
    assert _credits(_track(_performs(work)), standardize_artist_names=names).composers == (
        expected,
    )


def test_a_composer_without_a_sort_name_sorts_by_name() -> None:
    # Picard's `_select_sort_name_from_aliases` falls back to the name.
    anon = {"id": "art-anon", "name": "Anonymous", "sort-name": ""}
    work = _work("w-1", "Work", _rel("composer", anon))
    assert _credits(_track(_performs(work))).composers == (
        Person("Anonymous", "Anonymous", "art-anon"),
    )


def _sized(tracks: int, *, pregap: bool = False, data: int = 0) -> dict[str, Any]:
    medium: dict[str, Any] = {"track-list": [{"id": f"t{i}"} for i in range(tracks)]}
    if pregap:
        medium["pregap"] = {"id": "pregap"}
    if data:
        medium["data-track-list"] = [{"id": f"d{i}"} for i in range(data)]
    return {"id": "rel-big", "medium-list": [medium]}


@pytest.mark.parametrize(
    ("release", "answered"),
    [
        (_sized(500), True),
        (_sized(501), False),
        # The server loads every track's recording, so a pregap or a data track
        # takes a release over the limit as surely as an ordinary track.
        (_sized(500, pregap=True), False),
        (_sized(499, data=2), False),
    ],
)
def test_relationships_are_answered_up_to_five_hundred_recordings(
    release: dict[str, Any], answered: bool
) -> None:
    assert credits.relationships_answered(release) is answered


def test_relationships_are_answered_across_every_medium() -> None:
    release = _sized(300)
    release["medium-list"].append(_sized(201)["medium-list"][0])
    assert credits.relationships_answered(release) is False


# ---------- tagging the works (#680) ----------


def _with_aria(release: dict[str, Any]) -> dict[str, Any]:
    """`release` with its first track performing the Goldberg Aria."""
    release = copy.deepcopy(release)
    first = release["medium-list"][0]["track-list"][0]
    first["recording"]["work-relation-list"] = [_performs(ARIA)]
    return release


def _work_tags(path: Path) -> tuple[Any, Any]:
    now = formats_mod.read_owned(path)
    return now["works"], now["mb_work_ids"]


def test_a_tagging_writes_the_work_each_track_performs(album_with_tracks: Any) -> None:
    album_dir = album_with_tracks(2)
    tagger.tag_album(album_dir, _with_aria(_release_2_tracks()))

    first, second = sorted(album_dir.glob("*.m4a"))
    assert _work_tags(first) == ([ARIA["title"]], [ARIA["id"]])
    assert _work_tags(second) == ([], [])


def test_a_second_tagging_with_works_changes_nothing(album_with_tracks: Any) -> None:
    album_dir = album_with_tracks(2)
    release = _with_aria(_release_2_tracks())
    tagger.tag_album(album_dir, release)

    assert tagger.plan_album(album_dir, release).empty


def test_a_work_the_release_no_longer_links_is_removed(album_with_tracks: Any) -> None:
    # MusicBrainz answered, and its answer is no work: removed like any owned
    # field the release lacks.
    album_dir = album_with_tracks(2)
    tagger.tag_album(album_dir, _with_aria(_release_2_tracks()))
    tagger.tag_album(album_dir, _release_2_tracks())

    first = min(album_dir.glob("*.m4a"))
    assert _work_tags(first) == ([], [])


def test_a_release_too_large_to_answer_keeps_the_files_works(
    album_with_tracks: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The web service omits relationships above 500 recordings, and the response
    # then looks exactly like one with no works. The limit is lowered here so a
    # two-track release stands for a box set.
    from harmonist import activity_store

    activity_store.init(tmp_path / "audit.db")
    album_dir = album_with_tracks(2)
    tagger.tag_album(album_dir, _with_aria(_release_2_tracks()))
    recorded = len(_detail())
    monkeypatch.setattr(credits, "RELATIONSHIP_RECORDING_LIMIT", 1)

    unanswered = _release_2_tracks()
    assert tagger.plan_album(album_dir, unanswered).empty
    tagger.tag_album(album_dir, unanswered)

    first = min(album_dir.glob("*.m4a"))
    assert _work_tags(first) == ([ARIA["title"]], [ARIA["id"]])
    # Nor does the history claim a removal: what Undo would put back has to be
    # what the write did.
    assert len(_detail()) == recorded


FORMAT_FIXTURES = [("sine.m4a",), ("sine.mp3",), ("sine.flac",), ("sine.opus",)]


@pytest.mark.parametrize(("fixture",), FORMAT_FIXTURES)
def test_a_write_with_no_answer_keeps_the_works_on_the_file(tmp_path: Path, fixture: str) -> None:
    # The dispatcher's own guard, for a TagSet that reaches a write without
    # passing through the tagger.
    path = tmp_path / fixture
    shutil.copy(Path(__file__).parent / "fixtures" / fixture, path)
    base: dict[str, Any] = {
        "mb_album_id": "alb",
        "album": "Album",
        "album_artist": "Artist",
        "title": "Title",
        "artist": "Artist",
        "track_num": 1,
        "track_total": 1,
    }
    formats_mod.write_tags(
        path, formats_mod.TagSet(**base, works=["Aria"], mb_work_ids=["wk-1"]), None
    )

    formats_mod.write_tags(path, formats_mod.TagSet(**base), None)

    assert _work_tags(path) == (["Aria"], ["wk-1"])


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        # Arriving where there was none is Enrichment, unlike an artist id list.
        (None, ["wk-1"], Significance.ENRICHMENT),
        ([], ["wk-1", "wk-2"], Significance.ENRICHMENT),
        # Moving or going stays Identity, as every id does.
        (["wk-1"], ["wk-2"], Significance.IDENTITY),
        (["wk-1"], [], Significance.IDENTITY),
        (["wk-1"], ["wk-1", "wk-2"], Significance.IDENTITY),
    ],
)
def test_a_work_id_is_identity_unless_it_arrives(
    before: Any, after: Any, expected: Significance
) -> None:
    assert tagger.significance_of("mb_work_ids", before, after) is expected


def test_a_work_title_changing_is_enrichment() -> None:
    renamed = ["Goldberg-Variationen, BWV 988: Aria"], ["Goldberg Variations, BWV 988: Aria"]
    assert tagger.significance_of("works", *renamed) is Significance.ENRICHMENT


def test_the_album_page_names_a_work_by_its_title() -> None:
    assert tagger.mbid_names(_with_aria(_release_2_tracks()))[ARIA["id"]] == ARIA["title"]


# ---------- tagging the composers (#680) ----------


def _composed(*people: dict[str, Any]) -> dict[str, Any]:
    """The two-track release with track 1 performing a work by `people`."""
    release = copy.deepcopy(_release_2_tracks())
    work = _work("w-1", "Work", *people)
    release["medium-list"][0]["track-list"][0]["recording"]["work-relation-list"] = [
        _performs(work)
    ]
    return release


def _composer_tags(path: Path) -> tuple[Any, Any, Any]:
    now = formats_mod.read_owned(path)
    return now["composers"], now["composer_sort"], now["mb_composer_ids"]


def _verdict(album_dir: Path, release: dict[str, Any], tagging: TaggingChoices) -> Any:
    from harmonist import gardener

    plan = tagger.plan_album(album_dir, release, artwork=False, tagging=tagging)
    return gardener.verdict_for(plan)


def _overwrite_first(album_dir: Path, **values: Any) -> None:
    first = min(album_dir.glob("*.m4a"))
    formats_mod.write_owned(first, {**formats_mod.read_owned(first), **values})


AS_CREDITED = TaggingChoices(standardize_artist_names=ArtistNames.NONE)
VARIATIONS = TaggingChoices(standardize_artist_names=ArtistNames.VARIATIONS)


def test_a_tagging_writes_the_composers_as_three_aligned_lists(album_with_tracks: Any) -> None:
    album_dir = album_with_tracks(2)
    tagger.tag_album(album_dir, _composed(_rel("composer", RODGERS), _rel("composer", BACH)))

    assert _composer_tags(min(album_dir.glob("*.m4a"))) == (
        [RODGERS["name"], BACH["name"]],
        [RODGERS["sort-name"], BACH["sort-name"]],
        [RODGERS["id"], BACH["id"]],
    )


def test_a_credited_composer_follows_the_artist_name_setting(album_with_tracks: Any) -> None:
    # The file carries the credited spelling, which the release supports, and
    # the setting now selects the artist's own: the user's convention arriving,
    # as for an artist credit (#678).
    album_dir = album_with_tracks(2)
    release = _composed(_rel("composer", BACH, "J.S. Bach"))
    tagger.tag_album(album_dir, release, tagging=AS_CREDITED)

    assert _verdict(album_dir, release, VARIATIONS) is Significance.SETTINGS


def test_a_composer_from_another_tagger_is_replaced_as_enrichment(album_with_tracks: Any) -> None:
    # The common case on an adopted library: a CD-rip database's spelling with
    # no id. MusicBrainz's name and id arrive; nothing on the file is proven to
    # be somebody else.
    album_dir = album_with_tracks(2)
    release = _with_aria(_release_2_tracks())
    tagger.tag_album(album_dir, release)
    _overwrite_first(album_dir, composers=["Bach"], composer_sort=[], mb_composer_ids=[])

    assert _verdict(album_dir, release, TaggingChoices()) is Significance.ENRICHMENT


def test_a_composer_whose_id_moves_is_identity(album_with_tracks: Any) -> None:
    album_dir = album_with_tracks(2)
    release = _with_aria(_release_2_tracks())
    tagger.tag_album(album_dir, release)
    _overwrite_first(album_dir, mb_composer_ids=["somebody-else"])

    assert _verdict(album_dir, release, TaggingChoices()) is Significance.IDENTITY


def test_a_composer_without_an_id_is_removed_as_enrichment(album_with_tracks: Any) -> None:
    # MusicBrainz is canonical: a release that answers with no work removes a
    # composer that came from elsewhere (#680), and nothing moves an id.
    album_dir = album_with_tracks(2)
    release = _release_2_tracks()
    tagger.tag_album(album_dir, release)
    _overwrite_first(album_dir, composers=["Lindsey Buckingham"])

    assert _verdict(album_dir, release, TaggingChoices()) is Significance.ENRICHMENT


def test_the_album_page_names_a_composer_by_the_artists_own_name() -> None:
    release = _composed(_rel("composer", BACH, "J.S. Bach"))
    assert tagger.mbid_names(release)[BACH["id"]] == BACH["name"]


# ---------- lyricists and writers (#680) ----------


def test_a_tagging_writes_the_lyricist_and_the_writer(album_with_tracks: Any) -> None:
    album_dir = album_with_tracks(2)
    release = _composed(_rel("lyricist", HAMMERSTEIN), _rel("writer", NICKS))
    tagger.tag_album(album_dir, release)

    now = formats_mod.read_owned(min(album_dir.glob("*.m4a")))
    assert (now["lyricists"], now["writers"]) == ([HAMMERSTEIN["name"]], [NICKS["name"]])


def test_an_instrumental_track_is_tagged_with_no_lyricist(album_with_tracks: Any) -> None:
    album_dir = album_with_tracks(2)
    release = copy.deepcopy(_release_2_tracks())
    release["medium-list"][0]["track-list"][0]["recording"]["work-relation-list"] = [
        _performs(MY_FAVORITE_THINGS, "cover", "instrumental")
    ]
    tagger.tag_album(album_dir, release)

    now = formats_mod.read_owned(min(album_dir.glob("*.m4a")))
    assert (now["composers"], now["lyricists"]) == ([RODGERS["name"]], [])


# ---------- the native names, against Picard's own (#680) ----------
#
# Written out literally from Picard's `formats/` (release-3.0), not taken from
# Harmonist's constants: a test built from the constants can't see a name, a
# case or a frame that disagrees with Picard, which is the audit the tagging
# design asks for. The MP4 writer is lower case because Picard has no mapping
# for it and writes the tag name as it stands.
PICARD_CREDIT_KEYS = {
    "sine.m4a": {
        "\xa9wrk",
        "----:com.apple.iTunes:MusicBrainz Work Id",
        "\xa9wrt",
        "soco",
        "----:com.apple.iTunes:MusicBrainz Composer Id",
        "----:com.apple.iTunes:LYRICIST",
        "----:com.apple.iTunes:writer",
    },
    "sine.mp3": {
        "TXXX:WORK",
        "TXXX:MusicBrainz Work Id",
        "TCOM",
        "TSOC",
        "TXXX:MusicBrainz Composer Id",
        "TEXT",
        "TXXX:Writer",
    },
    "sine.flac": {
        "WORK",
        "MUSICBRAINZ_WORKID",
        "COMPOSER",
        "COMPOSERSORT",
        "MUSICBRAINZ_COMPOSERID",
        "LYRICIST",
        "WRITER",
    },
}


@pytest.mark.parametrize("fixture", sorted(PICARD_CREDIT_KEYS))
def test_credits_are_written_under_picards_names(tmp_path: Path, fixture: str) -> None:
    import mutagen

    path = tmp_path / fixture
    shutil.copy(Path(__file__).parent / "fixtures" / fixture, path)
    plain = mutagen.File(path)
    upper = fixture == "sine.flac"  # Vorbis keys are case-insensitive
    untouched = {k.upper() if upper else k for k in (plain.tags.keys() if plain.tags else [])}
    release = _composed(
        _rel("composer", BACH), _rel("lyricist", HAMMERSTEIN), _rel("writer", NICKS)
    )

    formats_mod.write_tags(path, tagger.tagsets_for(release, TaggingChoices())[0], None)

    tagged = mutagen.File(path)
    # `.keys()` is needed: iterating a Vorbis comment yields (key, value) pairs.
    keys = {k.upper() if upper else k for k in tagged.tags.keys()}  # noqa: SIM118
    assert PICARD_CREDIT_KEYS[fixture] <= keys - untouched
