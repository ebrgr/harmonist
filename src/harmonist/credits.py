"""Composer, lyricist, writer and work credits, read from a release (#680).

Picard writes these from MusicBrainz *relationships*, not from artist credits:
a recording is linked as a performance of one or more works, and the people who
wrote a work are credited on the work. A release can also credit a composer
directly, which soundtracks sometimes do. A track's credits are the release's
own, then those of each work it performs, as Picard's `track_to_metadata`
builds them on top of the release's metadata.

This module only reads. Which of these Harmonist writes, and how a change to
one is classified, belongs to `tagger` and `formats.owned`.

### Picard's rules, as `picard.mbjson` applies them

- Only the relationship types `_ARTIST_REL_TYPES` maps to these tags count:
  composer, lyricist (and librettist, which Picard writes as a lyricist) and
  writer. The rest, performers, producers, engineers, are outside #680.
- An **instrumental** performance credits no lyricist: the words were not
  performed. The work keeps them for its other recordings.
- A credited name ("credited as", `target-credit`) is spelled under the same
  #678 setting as an artist credit. The XML carries no aliases on a
  relationship's artist, so a former name cannot be recognised there, and a
  credit is standardized where Picard, reading the JSON, may keep it. That is
  the divergence #690 already records for artist credits.

### Where Harmonist departs from Picard

Picard collects the names, the sort names and the ids of composers into three
lists independently, each without duplicates, and players pair them up by
position. Two composers sharing a name then leave one name beside two ids, and
the pairing is wrong from that point on. Harmonist removes duplicates by
*person*, the name and the id together, so the three lists always line up. The
output is Picard's whenever no two people share a name and nobody is credited
under two spellings on one track.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from . import mb_tracks
from . import transforms as transforms_mod
from .models import Release, Track
from .transforms import TaggingChoices

#: The most recordings a release lookup returns relationships for. Above it the
#: MusicBrainz web service omits every recording's relationships, to avoid
#: timing out on audiobooks (`Controller/WS/2/Release.pm`,
#: `$max_recording_relationships`), and the response looks exactly like one
#: whose recordings have none.
RELATIONSHIP_RECORDING_LIMIT = 500

#: MusicBrainz relationship types, by the credit Picard writes for them
#: (`picard.mbjson._ARTIST_REL_TYPES`, the subset in scope).
_CREDIT_FOR_TYPE: dict[str, str] = {
    "composer": "composer",
    "lyricist": "lyricist",
    "librettist": "lyricist",
    "writer": "writer",
}


@dataclass(frozen=True)
class Person:
    """One credited person, spelled as the tags write them."""

    name: str
    sort_name: str
    mbid: str


@dataclass(frozen=True)
class Work:
    """One work a track performs."""

    title: str
    mbid: str


@dataclass(frozen=True)
class TrackCredits:
    """Everything this module reads for one track, each in Picard's order."""

    composers: tuple[Person, ...] = ()
    lyricists: tuple[Person, ...] = ()
    writers: tuple[Person, ...] = ()
    works: tuple[Work, ...] = ()


def relationships_answered(release: Release) -> bool:
    """Whether `release` can say what its tracks' credits are.

    False exactly when the web service would have left the recordings'
    relationships out for size. A release small enough to be answered, but with
    no relationships on any recording, has said its tracks have no works:
    that is an answer, and the credits are cleared on it like any other field
    MusicBrainz lacks.

    Counted over every track the service loads, the pregap and data tracks
    included, which can only overcount distinct recordings. Overcounting leaves
    a release's credits alone; undercounting would clear them on a box set.
    """
    recordings = 0
    for medium in release.get("medium-list") or []:
        recordings += len(mb_tracks.tracks_of(medium))
        recordings += len(medium.get("data-track-list") or [])
    return recordings <= RELATIONSHIP_RECORDING_LIMIT


def track_credits(release: Release, track: Track, choices: TaggingChoices) -> TrackCredits:
    """The credits Picard writes for `track` of `release`.

    Callers decide first whether the release answered at all
    (`relationships_answered`); this reads what is there, and a release whose
    relationships were left out reads as one with none.
    """
    composers: list[Person] = []
    lyricists: list[Person] = []
    writers: list[Person] = []
    works: list[Work] = []
    people = {"composer": composers, "lyricist": lyricists, "writer": writers}

    def credit(relations: Iterable[Mapping[str, Any]], instrumental: bool) -> None:
        for kind, person in _people(relations, choices):
            if instrumental and kind == "lyricist":
                continue
            if person not in people[kind]:
                people[kind].append(person)

    credit(release.get("artist-relation-list") or [], instrumental=False)
    recording = track.get("recording") or {}
    for relation in recording.get("work-relation-list") or []:
        if relation.get("type") != "performance" or not (work := relation.get("work")):
            continue
        if (found := Work(str(work.get("title") or ""), str(work["id"]))) not in works:
            works.append(found)
        instrumental = "instrumental" in (relation.get("attribute-list") or [])
        credit(work.get("artist-relation-list") or [], instrumental)
    return TrackCredits(tuple(composers), tuple(lyricists), tuple(writers), tuple(works))


def _people(
    relations: Iterable[Mapping[str, Any]], choices: TaggingChoices
) -> Iterator[tuple[str, Person]]:
    """Each in-scope relationship's credit and person, in MusicBrainz's order.

    The name goes through `transforms.artist_spelling`, the function every
    artist credit is spelled by, so a composer credited as a variation is
    standardized or kept exactly as a track artist would be. The sort name
    falls back to the name when MusicBrainz has none, as Picard's does.
    """
    for relation in relations:
        kind = _CREDIT_FOR_TYPE.get(str(relation.get("type")))
        artist = relation.get("artist")
        if kind is None or not artist or not artist.get("id"):
            continue
        entry = {"name": relation.get("target-credit") or artist.get("name"), "artist": artist}
        spelling = transforms_mod.artist_spelling(entry, choices)
        yield kind, Person(spelling.name, spelling.sort_name or spelling.name, str(artist["id"]))
