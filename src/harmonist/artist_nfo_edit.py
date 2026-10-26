"""Explicit local artist NFO edits; audio tags and artist identity never change.

The server discovers the target by artist ID, not by a submitted path. Existing
XML keeps unedited/unknown elements and gains an exact-byte backup before an
atomic replacement. A content fingerprint protects drafts against outside edits.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import stat
import tempfile
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from xml.etree import ElementTree as ET

from . import activity_store, artist_nfo, audit
from .artists import Artist, mbid

_LOCK = threading.Lock()
log = logging.getLogger(__name__)


class NfoChangedError(artist_nfo.NfoError):
    """The selected source changed or became ambiguous while it was edited."""


class HistoryNotFound(artist_nfo.NfoError):
    """No successful NFO write for this artist owns the requested event."""


@dataclass(frozen=True)
class Saved:
    path: Path
    changed: bool
    created: bool = False


def target(artist: Artist, profile: artist_nfo.Profile | None, info_dir: Path | None) -> Path:
    artist_id = mbid(artist.mbid or "")
    if artist_id is None:
        raise artist_nfo.NfoError("A MusicBrainz artist ID is required to edit an artist NFO.")
    if profile is not None:
        return profile.path.resolve()
    if info_dir is None:
        raise artist_nfo.NfoError("Configure an Artist Information Folder to create an artist NFO.")
    root = info_dir.resolve()
    folder = artist.key if artist.key.startswith("local-") else artist_id
    path = root / folder / "artist.nfo"
    if not path.resolve().is_relative_to(root):
        raise artist_nfo.NfoError("The artist NFO target is outside the Artist Information Folder.")
    return path


def normalise(values: Mapping[str, str]) -> dict[str, str]:
    """Only supported editable fields; repeated values occupy separate lines."""
    cleaned: dict[str, str] = {}
    for field in artist_nfo.EDIT_FIELDS:
        value = values.get(field.tag, "").strip().replace("\r\n", "\n")
        limit = 200_000 if field.tag == "biography" else 10_000 if field.multiple else 1_000
        if len(value) > limit:
            raise artist_nfo.NfoError(f"{field.label} exceeds {limit:,} characters.")
        if any(
            (ord(c) < 32 and c not in "\t\n\r")
            or 0xD800 <= ord(c) <= 0xDFFF
            or ord(c) in (0xFFFE, 0xFFFF)
            for c in value
        ):
            raise artist_nfo.NfoError(f"{field.label} contains characters XML cannot store.")
        if field.multiple:
            lines = tuple(dict.fromkeys(v.strip() for v in value.splitlines() if v.strip()))
            if len(lines) > 100 or any(len(v) > 200 for v in lines):
                raise artist_nfo.NfoError(
                    f"{field.label} allows 100 values of up to 200 characters."
                )
            value = "\n".join(lines)
        cleaned[field.tag] = value
    return cleaned


def _atomic_write(path: Path, data: bytes, mode: int, *, create: bool = False) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=".artist-nfo-", delete=False
        ) as out:
            temporary = Path(out.name)
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(temporary, mode)
        if create:
            # Publish a new file without replacing one created concurrently.
            os.link(temporary, path)
        else:
            os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def save(
    artist: Artist,
    music_dir: Path,
    info_dir: Path | None,
    backup_dir: Path,
    values: Mapping[str, str],
    expected: str,
    selection: str | None = None,
    *,
    confirmed_mbid: str | None = None,
    biography_source: Mapping[str, str] | None = None,
    discography: Sequence[artist_nfo.DiscographyAlbum] = (),
) -> Saved:
    """Write an explicit edit, with no-op, stale-draft and backup safeguards."""
    with _LOCK, activity_store.action():
        result = artist_nfo.for_artist(artist, music_dir, info_dir, selection, refresh=True)
        if result.profile is None and (result.candidates or selection):
            raise NfoChangedError("The NFO source changed. Read again and choose a source.")
        chosen_id = mbid(confirmed_mbid or artist.mbid or "")
        if chosen_id is None:
            raise artist_nfo.NfoError("A MusicBrainz artist ID is required to edit an artist NFO.")
        path = target(replace(artist, mbid=chosen_id), result.profile, info_dir)
        roots = [music_dir.resolve(), *([info_dir.resolve()] if info_dir is not None else [])]
        if not any(path.resolve().is_relative_to(root) for root in roots):
            raise artist_nfo.NfoError("The NFO target is outside the configured folders.")
        original: bytes | None = None
        mode = 0o644
        current: dict[str, str] = {}
        if result.profile is not None:
            with path.open("rb") as stream:
                original = stream.read(1024 * 1024 + 1)
            root = artist_nfo.parse_document(original, path)
            digest = hashlib.sha256(original).hexdigest()
            if digest != result.profile.fingerprint:
                raise NfoChangedError("The NFO changed while it was being read. Read again.")
            current = dict(result.profile.edit_values)
            mode = stat.S_IMODE(path.stat().st_mode)
        else:
            if path.exists():
                raise NfoChangedError("An NFO already exists at the target. Read again.")
            root = ET.Element("artist")
            ET.SubElement(root, "musicBrainzArtistID").text = chosen_id
            digest = ""
        cleaned = normalise(
            (current if original is not None else {"name": artist.name}) | dict(values)
        )
        changed = [f for f in artist_nfo.EDIT_FIELDS if cleaned[f.tag] != current.get(f.tag, "")]
        extra: dict[str, list[str]] = {}
        if confirmed_mbid is not None:
            old_id = result.profile.mbid if result.profile else ""
            if old_id != chosen_id:
                extra["musicBrainzArtistID"] = [old_id or "", chosen_id]
                for node in list(root):
                    if node.tag in {"musicBrainzArtistID", "musicbrainzartistid"}:
                        root.remove(node)
                ET.SubElement(root, "musicBrainzArtistID").text = chosen_id
            if artist.key != chosen_id and root.findtext(artist_nfo.CREDIT_TAG) != artist.key:
                extra["local_credit"] = [root.findtext(artist_nfo.CREDIT_TAG) or "", artist.key]
                for node in list(root.findall(artist_nfo.CREDIT_TAG)):
                    root.remove(node)
                ET.SubElement(root, artist_nfo.CREDIT_TAG).text = artist.key
        old_source = artist_nfo.biography_source(root)
        source = old_source
        if "biography" in values:
            if biography_source is not None:
                source = artist_nfo.validate_source(dict(biography_source))
            elif cleaned["biography"] != current.get("biography", ""):
                # Preserve attribution for a locally adapted article; clearing
                # the biography removes the attribution with the text.
                source = (
                    old_source | {"modified": "true"}
                    if old_source and cleaned["biography"]
                    else None
                )
        if source != old_source:
            extra["biography_source"] = [
                json.dumps(value, ensure_ascii=False, sort_keys=True) if value else ""
                for value in (old_source, source)
            ]
            for node in list(root.findall(artist_nfo.SOURCE_TAG)):
                root.remove(node)
            if source:
                ET.SubElement(root, artist_nfo.SOURCE_TAG, source)
        if discography:
            before = ET.tostring(root, encoding="unicode")
            for album in discography:
                if mbid(album.release_group_id or "") is None:
                    raise artist_nfo.NfoError("A discography selection needs a release-group ID.")
                matching = [
                    node
                    for node in root.findall("album")
                    if mbid(node.findtext("musicbrainzreleasegroupid", ""))
                    == album.release_group_id
                ]
                if not matching:
                    matching = [ET.SubElement(root, "album")]
                for node in matching:
                    # Preserve ordering, unknown children and attributes.
                    for tag, value in (
                        ("title", album.title),
                        ("year", album.year),
                        ("musicbrainzreleasegroupid", album.release_group_id),
                    ):
                        if not value:
                            # An absent provider value is never an instruction
                            # to clear metadata already present on disk.
                            continue
                        children = node.findall(tag)
                        if not children:
                            children = [ET.SubElement(node, tag)]
                        for child in children:
                            child.text = value
            after_xml = ET.tostring(root, encoding="unicode")
            if before != after_xml:

                def albums_text(document: ET.Element) -> str:
                    return "\n".join(
                        " · ".join(
                            n.findtext(k, "")
                            for k in ("title", "year", "musicbrainzreleasegroupid")
                        )
                        for n in document.findall("album")
                    )

                extra["discography"] = [albums_text(ET.fromstring(before)), albums_text(root)]
        if not changed and not extra and original is not None:
            return Saved(result.profile.path if result.profile else path, False)
        if expected != digest:
            raise NfoChangedError("The NFO changed since this editor was opened. Read again.")
        for field in changed:
            for element in list(root):
                if element.tag in (field.tag, *field.aliases):
                    root.remove(element)
            values_to_write = (
                cleaned[field.tag].splitlines() if field.multiple else [cleaned[field.tag]]
            )
            for value in values_to_write:
                if value:
                    ET.SubElement(root, field.tag).text = value
        data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        artist_nfo.parse_document(data, path)
        after = hashlib.sha256(data).hexdigest()
        backup: Path | None = None
        if original is not None:
            backup = _keep(backup_dir, original)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Recheck immediately before replacement, including an externally created
        # file. In-process edits are serialized; outside edits are never merged.
        if original is not None:
            if path.read_bytes() != original:
                raise NfoChangedError("The NFO changed before it could be saved. Read again.")
        elif path.exists():
            raise NfoChangedError("An NFO appeared before it could be created. Read again.")
        audit.record(
            "artist_nfo.write",
            artist_id=artist.key,
            file=path,
            before=digest or "absent",
            after=after,
            backup=backup,
            fields=",".join([f.tag for f in changed] + list(extra)),
        )
        if original is None:
            _atomic_write(path, data, mode, create=True)
        else:
            _atomic_write(path, data, mode)
        _record_saved(
            artist,
            path,
            digest or None,
            after,
            {field.tag: [current.get(field.tag, ""), cleaned[field.tag]] for field in changed}
            | extra,
        )
        return Saved(result.profile.path if result.profile else path, True, original is None)


def _keep(directory: Path, data: bytes) -> Path:
    """No verified backup means no replacement, including an Undo."""
    digest = hashlib.sha256(data).hexdigest()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{digest}.nfo"
    if not path.exists() and not path.is_symlink():
        _atomic_write(path, data, 0o600)
    if path.is_symlink() or not path.is_file() or path.read_bytes() != data:
        raise artist_nfo.NfoError("The existing NFO backup does not match its digest.")
    return path


def _record_saved(
    artist: Artist,
    path: Path,
    before: str | None,
    after: str | None,
    changes: dict[str, list[str]],
    *,
    restored: bool = False,
) -> None:
    event_id = audit.record(
        "artist_nfo.restored" if restored else "artist_nfo.saved",
        artist_id=artist.key,
        file=path,
        digest=after,
    )
    if event_id is not None:
        activity_store.record_nfo_change(
            activity_store.NfoChange(event_id, str(path.resolve()), before, after, changes)
        )


def backup_path(directory: Path, digest: str) -> Path:
    """Resolve only content addresses; preserve access to earlier backup storage."""
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise artist_nfo.NfoError("Invalid NFO backup digest.")
    path = directory / f"{digest}.nfo"
    if not path.exists():
        legacy = directory.parent / "artist-nfo-backups" / f"{digest}.nfo"
        if legacy.exists():
            path = legacy
    if path.is_symlink() or not path.is_file():
        raise artist_nfo.NfoError("The original NFO backup is unavailable.")
    return path


def _restore_target(
    change: activity_store.NfoChange,
    music_dir: Path,
    info_dir: Path | None,
) -> Path:
    path = Path(change.file)
    roots = [music_dir.resolve(), *([info_dir.resolve()] if info_dir is not None else [])]
    if (
        path.is_symlink()
        or path.name != "artist.nfo"
        or not any(path.resolve().is_relative_to(root) for root in roots)
    ):
        raise artist_nfo.NfoError(
            "The recorded NFO is outside the configured folders or is a link."
        )
    return path


def undo_unavailable(
    change: activity_store.NfoChange,
    profile: artist_nfo.Profile | None,
    music_dir: Path,
    info_dir: Path | None,
    backup_dir: Path,
) -> str | None:
    """A visible reason replaces Undo where the current file/backup cannot support it."""
    try:
        path = _restore_target(change, music_dir, info_dir)
        if change.after_hash is None:
            if path.exists():
                return "An NFO has been created since this entry."
        elif (
            profile is None
            or profile.path.resolve() != path
            or profile.fingerprint != change.after_hash
        ):
            return "The NFO changed since this entry, or its source is not selected."
        if change.before_hash is not None:
            data = backup_path(backup_dir, change.before_hash).read_bytes()
            if hashlib.sha256(data).hexdigest() != change.before_hash:
                return "The original NFO backup does not match its digest."
    except artist_nfo.NfoError as exc:
        # Backups explicitly removed by the owner are genuinely unavailable,
        # not a failed query presented as an empty history.
        return str(exc)
    except OSError:
        log.exception("Could not inspect an NFO backup")
        return "Could not read the NFO backup. Check folder permissions."
    return None


def restore(
    artist: Artist,
    music_dir: Path,
    info_dir: Path | None,
    backup_dir: Path,
    event_id: int,
) -> Saved:
    """Undo one successful whole-file write, with a backup of what it replaces.

    A request identifies an artist's event, never a filename or content address.
    Current bytes must still match that event. External edits are never merged.
    """
    with _LOCK, activity_store.action():
        events = activity_store.artist_history(artist.key)
        change = activity_store.nfo_changes_for([e.id for e in events]).get(event_id)
        if change is None:
            raise HistoryNotFound("No NFO change belongs to this artist and history entry.")
        path = _restore_target(change, music_dir, info_dir)
        current = path.read_bytes() if path.exists() else None
        digest = hashlib.sha256(current).hexdigest() if current is not None else None
        if digest == change.before_hash:
            return Saved(path, False)
        if digest != change.after_hash:
            raise NfoChangedError("The NFO changed since this history entry. Read again.")
        original: bytes | None = None
        if change.before_hash is not None:
            original = backup_path(backup_dir, change.before_hash).read_bytes()
            if hashlib.sha256(original).hexdigest() != change.before_hash:
                raise artist_nfo.NfoError("The NFO backup does not match its digest.")
            root = artist_nfo.parse_document(original, path)
            ids = [
                mbid(e.text or "")
                for e in root
                if e.tag in ("musicBrainzArtistID", "musicbrainzartistid")
            ]
            if set(ids) not in ({artist.mbid}, {mbid(artist.key)}) and not (
                root.findtext(artist_nfo.CREDIT_TAG) == artist.key and None not in ids and ids
            ):
                raise artist_nfo.NfoError("The NFO backup has a different artist identity.")
        backup = _keep(backup_dir, current) if current is not None else None
        if (path.read_bytes() if path.exists() else None) != current:
            raise NfoChangedError("The NFO changed before it could be restored. Read again.")
        mode = stat.S_IMODE(path.stat().st_mode) if current is not None else 0o644
        audit.record(
            "artist_nfo.restore",
            artist_id=artist.key,
            file=path,
            before=digest,
            after=change.before_hash,
            backup=backup,
            event_id=event_id,
        )
        if original is None:
            path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(path, original, mode, create=current is None)
        _record_saved(
            artist,
            path,
            digest,
            change.before_hash,
            {key: [pair[1], pair[0]] for key, pair in change.changes.items()},
            restored=True,
        )
        return Saved(path, True, current is None)
