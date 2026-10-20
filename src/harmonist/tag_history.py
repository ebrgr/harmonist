"""Turning stored tag-change records into something readable (#86).

One tagging writes one record per *file* — that granularity is what makes the
record exactly reversible, since an album tagged unevenly over the years has
different "before" values on different tracks. But it is not how anyone reads
history. Nobody asks "what happened to track 9?"; they ask "what did it
change?", and an eighteen-track album that answers the first question makes the
second one unanswerable by burying it in eighteen near-identical rows.

So this module inverts the records: **one row per field**, with how far it
reached stated beside it. The height of the result is bounded by the number of
fields that changed, not by the size of the album — which matters because the
gardener (#32) will eventually write one of these per album per night.

Pure functions over values, like `compare`: no I/O, no store, no templates. It
borrows `compare.diff_runs` for in-value emphasis rather than growing a second
implementation, because a change marked one way on the album panel and another
way in history would read as two different kinds of fact.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from . import album_files, formats
from .compare import Run, diff_runs
from .formats.owned import ARTWORK, LABELS, SCOPE, Owned, Scope

#: Local alias for the shared label map, which moved to `owned` so the album
#: comparison can build its rows from it too (#295) — one name per field for
#: History, the re-tag plan and the comparison alike.
_LABELS = LABELS


#: The one order every list of tag changes is shown in: a History entry, the
#: album page's waiting update, and the Inbox's (#273). What a person reads
#: comes before what a player files by, and identifiers last, since a
#: MusicBrainz id moving says little until its title does. It used to be the
#: order `Owned` happens to declare its fields in, which put the release id at
#: the top of a History entry.
#:
#: Every owned field is listed, and a test holds that, so a field added later
#: cannot silently sort to the end. Artwork comes after them all: it is not an
#: owned field, and it is the one row whose value is not a tag.
_FIELD_ORDER: tuple[Owned, ...] = (
    Owned.ALBUM,
    Owned.ALBUM_ARTIST,
    Owned.TITLE,
    Owned.ARTIST,
    Owned.TRACK_TOTAL,
    Owned.TRACK_NUM,
    Owned.DISC_TOTAL,
    Owned.DISC_NUM,
    Owned.DATE,
    Owned.ORIGINAL_DATE,
    Owned.LABEL,
    Owned.CATALOG_NUMBER,
    Owned.MB_ALBUM_TYPE,
    Owned.MB_ALBUM_STATUS,
    Owned.MB_ALBUM_COUNTRY,
    Owned.MEDIA,
    Owned.DISC_SUBTITLE,
    Owned.COMPILATION,
    Owned.ALBUM_ARTISTS,
    Owned.ARTISTS,
    Owned.ALBUM_ARTIST_SORT,
    Owned.ARTIST_SORT,
    Owned.SCRIPT,
    Owned.BARCODE,
    Owned.ASIN,
    Owned.ISRCS,
    Owned.MB_ALBUM_ID,
    Owned.MB_RELEASE_GROUP_ID,
    Owned.MB_ALBUM_ARTIST_IDS,
    Owned.MB_TRACK_ID,
    Owned.MB_RELEASE_TRACK_ID,
    Owned.MB_ARTIST_IDS,
)
_ORDER: dict[str, int] = {f.value: i for i, f in enumerate(_FIELD_ORDER)} | {
    ARTWORK: len(_FIELD_ORDER)
}


def field_rank(field: str) -> int:
    """Where `field` comes in a list of tag changes: lower comes first."""
    return _ORDER.get(field, len(_ORDER))


#: The owned fields whose values are MusicBrainz identifiers, and the kind of
#: entity each names, which is also its path on musicbrainz.org (#801).
ID_ENTITIES: dict[str, str] = {
    Owned.MB_ALBUM_ID.value: "release",
    Owned.MB_RELEASE_GROUP_ID.value: "release-group",
    Owned.MB_ALBUM_ARTIST_IDS.value: "artist",
    Owned.MB_ARTIST_IDS.value: "artist",
    Owned.MB_TRACK_ID.value: "recording",
    Owned.MB_RELEASE_TRACK_ID.value: "track",
}

#: How many tracks a partial change names in its reach before counting them
#: instead (#820). Past three, "tracks 2, 5, 9, 14" is harder to read than the
#: count it replaces.
NAMED_TRACKS = 3

#: How a stored value joins several ids: `display`'s "; " for a list, and the
#: "/" some taggers put inside a single value. Captured so a row can keep them,
#: since one slash-joined value becoming two values is itself the change.
_ID_SEPARATOR = re.compile(r"(\s*[;/]\s*)")


@dataclass(frozen=True)
class IdRef:
    """One MusicBrainz id in a change row, with its name where one is known."""

    id: str
    name: str | None
    entity: str

    @property
    def label(self) -> str:
        """The name, or the id cut short: the full id is the link's tooltip,
        and its first eight characters are enough to tell two apart."""
        return self.name if self.name is not None else f"{self.id[:8]}…"

    @property
    def url(self) -> str:
        return f"https://musicbrainz.org/{self.entity}/{self.id}"


def named(rows: Sequence[FieldChange], names: Mapping[str, str]) -> tuple[FieldChange, ...]:
    """`rows`, with each id row carrying the names `names` has for its ids
    (`tagger.change_names` builds those from a release).

    Only the names a row needs travel with it, so a row is complete on its own
    wherever it is rendered: History, the album page, the Inbox.
    """
    result = []
    for row in rows:
        if not row.is_id:
            result.append(row)
            continue
        values = [row.before, row.after] + [v for t in row.variants for v in (t.before, t.after)]
        ids = {part for value in values for part in _id_tokens(value)}
        known = tuple(sorted((i, names[i]) for i in ids if i in names))
        result.append(replace(row, names=known))
    return tuple(result)


def _id_tokens(value: str | None) -> list[str]:
    """The ids in one displayed value, separators dropped."""
    if not value:
        return []
    return [p for i, p in enumerate(_ID_SEPARATOR.split(value)) if i % 2 == 0 and p]


@dataclass(frozen=True)
class TrackChange:
    """What one file's copy of a field did, as its own line under the field."""

    file: str
    position: str | None
    before: str | None
    after: str | None
    #: The same in-value emphasis a uniform row's pair gets (#820): each line is
    #: read on its own, so what changed within it is what the reader looks for.
    before_runs: tuple[Run, ...] = ()
    after_runs: tuple[Run, ...] = ()


@dataclass(frozen=True)
class FieldChange:
    """One row of the field-first view: what changed, and how far it reached."""

    field: str
    label: str
    before: str | None
    after: str | None
    #: How many of the tagging's files carried this change, and how many files
    #: it touched in total. `tracks == total` is the ordinary case.
    tracks: int
    total: int
    #: True when the values are digests rather than anything a person reads —
    #: artwork. The row states what happened ("replaced") instead of reciting
    #: 128 hex characters that would dominate every row around it. The digests
    #: are still in the record, where #131 needs them.
    opaque: bool = False
    #: None for artwork, which is deliberately not an owned field and so has no
    #: album/track scope of its own.
    scope: Scope | None = None
    before_runs: tuple[Run, ...] = ()
    after_runs: tuple[Run, ...] = ()
    #: Every file's version of this change, in the order recorded. Populated
    #: only when they DIFFER — when the whole album moved the same way, the
    #: pair above already says everything. When they differ, these are the only
    #: honest values there are, and the row shows them, one line per track.
    variants: tuple[TrackChange, ...] = ()
    #: Where each track this change reached sits on the album, in file order —
    #: what `reach` names when there are few of them (#820). None for a track
    #: whose position is unknown.
    positions: tuple[str | None, ...] = ()
    #: The folder cover's name, when this artwork change reached it too. It is
    #: not a track, so it is named beside the track count rather than counted
    #: in it (#469).
    cover: str | None = None
    #: For an id row, the names known for its ids, as `named` attaches them.
    names: tuple[tuple[str, str], ...] = ()

    @property
    def uniform(self) -> bool:
        """Whether every file that changed changed the same way."""
        return not self.variants

    @property
    def is_id(self) -> bool:
        """Whether this row's values are MusicBrainz ids (#801)."""
        return self.field in ID_ENTITIES

    def parts(self, value: str | None) -> tuple[IdRef | str, ...]:
        """An id row's value as ids and the separators between them, in order."""
        if not value:
            return ()
        known = dict(self.names)
        entity = ID_ENTITIES[self.field]
        return tuple(
            IdRef(part, known.get(part), entity) if i % 2 == 0 else part
            for i, part in enumerate(_ID_SEPARATOR.split(value))
            if part
        )

    def refs(self, value: str | None) -> tuple[IdRef, ...]:
        """The ids in one of this row's values."""
        return tuple(p for p in self.parts(value) if isinstance(p, IdRef))

    @property
    def summary(self) -> str:
        """What to show in place of the values, for a row whose values are not
        readable. "Replaced" and "added" are different facts to a user deciding
        whether they mind."""
        if self.before is None:
            return "added"
        if self.after is None:
            return "removed"
        return "replaced"

    @property
    def reach(self) -> str:
        """The annotation beside the value: how much of the album this touched.

        An album-scoped field that moved on every track is just "album" — it is
        one logical change, and saying "all 18 tracks" would invite the reader
        to check eighteen things. A field that moved on only some tracks always
        gets the count, whatever its scope, because that is the case where the
        album has no single answer.

        Up to `NAMED_TRACKS` of them are named instead, when the change was the
        same on each (#820): "Coma → COMA, 1 of 28 tracks" left the reader to
        go and find track 6. A change that differed per track numbers its own
        lines, so naming the tracks here too would say it twice.

        The folder cover is named rather than counted: "cover.jpg" alone when
        it was the only thing an artwork change reached, and after the tracks
        when it was one of several.
        """
        if self.cover is not None and not self.tracks:
            return self.cover
        named = [p for p in self.positions if p is not None]
        if self.uniform and self.tracks < self.total and len(named) == self.tracks <= NAMED_TRACKS:
            reach = f"track {named[0]}" if len(named) == 1 else f"tracks {', '.join(named)}"
        elif self.tracks < self.total:
            reach = f"{self.tracks} of {self.total} tracks"
        elif self.scope is Scope.ALBUM:
            reach = "album"
        else:
            reach = "all tracks" if self.total > 1 else "1 track"
        return f"{reach} and {self.cover}" if self.cover is not None else reach


def group_by_action(
    events: Sequence[Any],
    detail: Mapping[int, Any],
) -> dict[int, tuple[FieldChange, ...]]:
    """Attach each tagging's field-first summary to the history row that should
    carry it, as `{event_id: rows}`.

    One tagging writes one `tag.track` row per file, all sharing an `action_id`
    with the activity entry above them (#84). The summary hangs off **that
    activity entry** — "Re-tagged" — rather than off any one file's audit line.

    Two reasons, both learned from looking at the rendered page. History is
    newest-first, so anchoring to the earliest audit row put the summary at the
    BOTTOM of the group, reading as though it described only the last file
    listed. And the activity entry is the user-facing record of the action,
    so the summary survives the "Show details" toggle that hides raw audit text
    — which is right, because a plain-language list of what changed is exactly
    the kind of thing that toggle is meant to leave visible.

    Falls back to the earliest audit row when an action has no activity entry
    (a background pass with no user-facing outcome).

    Rows written before #84 landed carry no `action_id` at all. They degrade to
    per-event summaries — each file's changes against its own audit line —
    which is honest about what was recorded at the time and needs no back-fill
    of data nobody captured.
    """
    return {
        anchor: rows
        for anchor, records in group_records(events, detail).items()
        if (rows := summarise(records))
    }


def group_records(events: Sequence[Any], detail: Mapping[int, Any]) -> dict[int, list[Any]]:
    """The per-file records of each tagging, keyed by the history row that
    should carry them.

    Separated from `group_by_action` because two callers need this grouping and
    they must not disagree about it: the page renders a summary from it, and
    Undo acts on it. If Undo restored a different set of records from the ones
    the summary described, the button would do something other than what the
    user just read.

    Records come back in file order — the order they were written.
    """
    if not detail:
        return {}

    with_detail: dict[str, list[Any]] = {}
    anchors: dict[str, Any] = {}
    out: dict[int, list[Any]] = {}
    for event in events:
        action_id = event.action_id
        if action_id is None:
            if event.id in detail:
                out[event.id] = [detail[event.id]]
            continue
        if event.source == "activity":
            # The action's user-facing outcome. First one wins; there is only
            # ever one per action, but a defensive `setdefault` beats assuming.
            anchors.setdefault(action_id, event)
        elif event.id in detail:
            with_detail.setdefault(action_id, []).append(event)

    for action_id, grouped in with_detail.items():
        ordered = sorted(grouped, key=lambda e: e.id)
        anchor = anchors.get(action_id) or ordered[0]
        out[anchor.id] = [detail[e.id] for e in ordered]
    return out


@dataclass(frozen=True)
class ArtworkRevert:
    """One file's share of an artwork Undo (#131, #471).

    `before` is the image to put back, or None where the change ADDED an image
    to a file that had none — undoing that means taking it off again. `after`
    is the image the change left, and it is what proves the file still carries
    this change: one changed since is left alone, as a tag Undo leaves a field.
    """

    file: str
    before: str | None
    after: str


def artwork_revert_plan(records: Sequence[Any]) -> tuple[ArtworkRevert, ...]:
    """What an artwork Undo of one change would do, per file.

    Additions and replacements alike (#471). An addition used to be left out —
    a track that gained art was not "restored to no art" — which made the
    artwork half of a change undoable only when it had overwritten something,
    and left the folder cover a change created with no way back at all. Both
    sides are recorded, so both can be undone; the per-file check on `after` is
    what keeps an image the user has put there since from being taken away.

    A pair with no `after` records a removal, which is not an artwork change
    this undoes. Records written before a change carried its `after` digest do
    not exist, so nothing here has to guess one.
    """
    out: list[ArtworkRevert] = []
    for record in records:
        pair = _pair(record.changes.get(ARTWORK))
        if pair is None:
            continue
        before, after = pair
        if not isinstance(after, str) or not after:
            continue
        was = before if isinstance(before, str) and before else None
        if was != after:
            out.append(ArtworkRevert(file=record.file, before=was, after=after))
    return tuple(out)


def label_for(field: str) -> str:
    """The human name of one owned field, for prose outside the change table.

    Falls back to the raw key for the same reason `_LABELS` does: these records
    are permanent and unversioned, so a field this build has since renamed must
    degrade to showing its name rather than raising.
    """
    return _LABELS.get(field, field)


@dataclass(frozen=True)
class FileRevert:
    """One file's share of an undo: what to put back, and how to find the file.

    `fields` is `{owned_field: (before, after)}` — both sides, because the
    *after* is what proves the file still carries what this tagging wrote. A
    field changed since (a later re-tag, an edit in Picard) must be left alone,
    and only the recorded after-value can tell the difference.
    """

    file: str
    fields: dict[str, tuple[Any, Any]]
    #: The other three ways this record names its track (see the 5->6 migration).
    #: Used when the filename is gone: release-track id, recording + position,
    #: then position, each scoped to this album and required to be unique.
    track_ref: str | None = None
    rec_ref: str | None = None
    position: str | None = None


def revert_plan(records: Sequence[Any]) -> tuple[FileRevert, ...]:
    """What an Undo of one tagging would put back, per file.

    Built from the same `group_records` output the page's summary is built from,
    so Undo cannot act on a different set of files from the one the user just
    read — the reason that grouping is a shared function rather than inlined
    into `group_by_action`.

    Two fields are deliberately absent:

    * **artwork**, which is not an owned tag and has its own undo and its own
      store (#131). One button per store, each honest about what it can do.
    * nothing else here — the `mb_album_id` guard lives in `tagger.revert_tags`,
      because whether reverting it would strand the album is a question about
      the sidecar, which this module has no business reading.

    Files whose record holds no revertable field are dropped rather than
    returned empty, so a caller can treat an empty result as "nothing to undo".
    """
    plan: list[FileRevert] = []
    for record in records:
        fields: dict[str, tuple[Any, Any]] = {}
        for field, entry in record.changes.items():
            if field == ARTWORK:
                continue
            pair = _pair(entry)
            if pair is None:
                continue
            fields[field] = (pair[0], pair[1])
        if fields:
            plan.append(
                FileRevert(
                    file=record.file,
                    fields=fields,
                    track_ref=record.track_ref,
                    rec_ref=record.rec_ref,
                    position=record.position,
                )
            )
    return tuple(plan)


def display(value: Any) -> str | None:
    """One stored value as the page shows it, or None when it was absent.

    `None`, `""` and `[]` all collapse to absent, matching `owned._absent` —
    the record stores raw values so a revert can restore them exactly, but the
    reader must not draw a distinction the tagger doesn't make.
    """
    if value is None or value == "" or value == []:
        return None
    if isinstance(value, (list, tuple)):
        return "; ".join(str(v) for v in value)
    return str(value)


def _pair(entry: Any) -> tuple[Any, Any] | None:
    """A stored `[before, after]`, or None if it isn't one.

    Defensive on purpose: the payload is permanent, unversioned JSON, so a row
    written by a future build may hold a shape this one doesn't expect. Skipping
    it loses one row; raising loses the page.
    """
    if isinstance(entry, (list, tuple)) and len(entry) == 2:
        return entry[0], entry[1]
    return None


@dataclass(frozen=True)
class _PlannedFile:
    """One file's share of a re-tag that has NOT happened, shaped like the
    stored record `summarise` expects — `file`, `position`, `changes`.

    Deliberately structural rather than a shared base class: `summarise` is
    already duck-typed over `activity_store.TagChanges` because the two live in
    different layers, and this is the second caller taking advantage of it, not
    a new coupling.
    """

    file: str
    position: str | None
    changes: Mapping[str, Any]


def from_plan(plan: Any, album_dir: Path, files: Sequence[Path]) -> tuple[FieldChange, ...]:
    """The same field-first rows as `summarise`, for a re-tag not yet done.

    This is what lets one renderer serve two questions — *what did that tagging
    change?* and *what would this one change?* — so an update waiting on an
    album reads exactly like the History entry it will become (#291). Two views
    of one plan would drift, and the second would be the one nobody noticed had
    gone wrong.

    `files` is **every** file in the album, not just the ones the plan touches:
    `summarise` takes its `total` from the record count, and that total is what
    `reach` renders. Passing only the changed files would report "all tracks"
    for a field that moved on three of twenty-seven.

    `position` is the one the tagging would record (`AlbumPlan.positions`), so
    each per-track line is numbered as its History record will be — "2-1" for
    the first track of a second disc, which file order called "3" (#820).
    """
    naming = album_files.Naming(album_dir, files)
    return summarise(
        [
            _PlannedFile(
                file=naming.name_of(path),
                position=plan.positions.get(path),
                changes=plan.changes.get(path, {}),
            )
            for path in files
        ]
    )


def summarise(records: Sequence[Any]) -> tuple[FieldChange, ...]:
    """Invert per-file records into field-first rows.

    `records` is one tagging's `activity_store.TagChanges`, in the order they
    were written (which is file order). The result is ordered by `_ORDER`, so
    album-level fields come before per-track ones and artwork sits last.

    `total` counts TRACKS. A tagging that also created or replaced the folder
    cover records that as a file of its own, and counting it would report a
    field that reached all thirteen tracks as "13 of 14" (#469).
    """
    if not records:
        return ()
    total = sum(1 for record in records if _is_track(record.file))

    # field -> [(file, position, before, after)], in file order.
    by_field: dict[str, list[tuple[str, str | None, Any, Any]]] = {}
    for record in records:
        for field, entry in record.changes.items():
            pair = _pair(entry)
            if pair is None:
                continue
            by_field.setdefault(field, []).append((record.file, record.position, *pair))

    rows = [_row(field, entries, total) for field, entries in by_field.items()]
    return tuple(sorted(rows, key=lambda r: (_ORDER.get(r.field, len(_ORDER)), r.label)))


def _is_track(file: str) -> bool:
    """Whether a record names a track rather than the folder cover. By the name's
    extension alone — no file is opened — so it holds for a record whose file has
    since gone."""
    return formats.is_supported(Path(file))


def _row(field: str, entries: list[tuple[str, str | None, Any, Any]], total: int) -> FieldChange:
    """One field's row: the representative change, and the outliers if any."""
    pairs = [(display(b), display(a)) for _, _, b, a in entries]
    before, after = _representative(pairs)

    opaque = field == ARTWORK
    # Two sha256 digests differ in nearly every character, so emphasis on them
    # would be noise on top of noise; so do two MusicBrainz ids, which a row
    # names rather than spells out (#801).
    emphasised = not opaque and field not in ID_ENTITIES

    variants: tuple[TrackChange, ...] = ()
    if len(set(pairs)) > 1:
        # Only when they actually differ — a compilation where every track's
        # artist changed to something different has no single answer, so the
        # row shows each track's own line instead (#820).
        variants = tuple(
            TrackChange(f, p, b, a, *_runs(b, a, emphasised))
            for (f, p, _, _), (b, a) in zip(entries, pairs, strict=True)
        )

    # A row whose files disagree shows no pair to emphasise.
    before_runs, after_runs = _runs(before, after, emphasised and not variants)

    return FieldChange(
        field=field,
        label=_LABELS.get(field, field),
        before=before,
        after=after,
        opaque=opaque,
        tracks=sum(1 for f, _, _, _ in entries if _is_track(f)),
        total=total,
        cover=next((f for f, _, _, _ in entries if not _is_track(f)), None),
        scope=SCOPE.get(Owned(field)) if field in _ORDER and field != ARTWORK else None,
        before_runs=before_runs,
        after_runs=after_runs,
        variants=variants,
        positions=tuple(p for f, p, _, _ in entries if _is_track(f)),
    )


def _runs(
    before: str | None, after: str | None, emphasised: bool
) -> tuple[tuple[Run, ...], tuple[Run, ...]]:
    """The emphasis for one before-and-after pair: none unless both sides exist
    and the field is one that emphasis helps."""
    if before is None or after is None or not emphasised:
        return (), ()
    return diff_runs(before, after)


def _representative(pairs: list[tuple[str | None, str | None]]) -> tuple[str | None, str | None]:
    """The album's `(before, after)` for this field, where it has one.

    When every file agrees this is trivially that pair — the ordinary case, and
    the only one the value cell renders since #185: a row whose files disagree
    shows each track's own line instead (#820), because no single pair is the
    album's.

    It still resolves the disagreeing case for **artwork**, whose row shows
    what happened rather than a value, and whose `before` decides whether the
    restore button is offered at all. There the most common `(before, after)`
    is the right answer — an album where one track never had an image should
    still offer to put back the image the other seventeen lost.

    The pair is kept together rather than resolved field-by-field: taking the
    most common `before` and the most common `after` independently could
    manufacture a transition that no track actually made. On a tie it is **the
    first file's**, the same rule `compare.consensus` uses for the album panel.
    """
    counts = Counter(pairs)
    top = max(counts.values())
    tied = {p for p, c in counts.items() if c == top}
    return next(p for p in pairs if p in tied)
