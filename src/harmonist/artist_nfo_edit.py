"""Explicit local artist NFO edits; audio tags and artist identity never change.

The server discovers the target by artist ID, not by a submitted path. Existing
XML keeps unedited/unknown elements and gains an exact-byte backup before an
atomic replacement. A content fingerprint protects drafts against outside edits.
"""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

from . import artist_nfo, audit
from .artists import Artist, mbid

_LOCK = threading.Lock()


class NfoChangedError(artist_nfo.NfoError):
    """The selected source changed or became ambiguous while it was edited."""


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
    path = root / artist_id / "artist.nfo"
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
) -> Saved:
    """Write an explicit edit, with no-op, stale-draft and backup safeguards."""
    with _LOCK:
        result = artist_nfo.for_artist(artist, music_dir, info_dir, selection, refresh=True)
        if result.profile is None and (result.candidates or selection):
            raise NfoChangedError("The NFO source changed. Read again and choose a source.")
        path = target(artist, result.profile, info_dir)
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
            ET.SubElement(root, "musicBrainzArtistID").text = artist.mbid
            digest = ""
        cleaned = normalise(
            (current if original is not None else {"name": artist.name}) | dict(values)
        )
        changed = [f for f in artist_nfo.EDIT_FIELDS if cleaned[f.tag] != current.get(f.tag, "")]
        if not changed and original is not None:
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
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup = backup_dir / f"{digest}.nfo"
            if backup.exists():
                if backup.is_symlink() or not backup.is_file() or backup.read_bytes() != original:
                    raise artist_nfo.NfoError("The existing NFO backup does not match its digest.")
            else:
                _atomic_write(backup, original, 0o600)
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
            artist=artist.mbid,
            file=path,
            before=digest or "absent",
            after=after,
            backup=backup,
            fields=",".join(f.tag for f in changed),
        )
        if original is None:
            _atomic_write(path, data, mode, create=True)
        else:
            _atomic_write(path, data, mode)
        audit.record("artist_nfo.saved", artist=artist.mbid, file=path, digest=after)
        return Saved(result.profile.path if result.profile else path, True, original is None)
