"""Artist query cache and immutable, local-input-bound import reviews.

Only prepare/extend perform network work. Applying a review has no network
dependency and never substitutes a newer cached answer for the reviewed one.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from . import activity_store, artist_lookup, artist_nfo, artist_nfo_edit
from .artists import Artist, mbid

LANGUAGES = artist_lookup.LANGUAGES
log = logging.getLogger(__name__)


class ReviewError(artist_nfo.NfoError):
    """The reviewed evidence or its local target is no longer available."""


def _key(*parts: object) -> str:
    return json.dumps(parts, ensure_ascii=False, separators=(",", ":"))


def _query(
    key: str, fetch: Callable[[], dict[str, Any]], *, refresh: bool
) -> activity_store.ArtistDocument:
    stored = activity_store.artist_document(key)
    if stored and not refresh and datetime.now(UTC) - stored.fetched_at < timedelta(hours=1):
        return stored
    payload = fetch()
    if payload and ("albums" not in payload or payload["albums"]):
        activity_store.store_artist_document(key, payload)
    return activity_store.ArtistDocument(payload, datetime.now(UTC))


def _artist(artist_id: str, *, refresh: bool) -> activity_store.ArtistDocument:
    includes = "+".join(sorted(artist_lookup.ARTIST_INCLUDES))
    key = _key("musicbrainz", "artist", artist_id, includes)
    stored = activity_store.artist_document(key)
    if stored and not refresh and datetime.now(UTC) - stored.fetched_at < timedelta(hours=1):
        return stored
    payload = artist_lookup.fetch_artist(artist_id)
    canonical = mbid(str(payload.get("id", "")))
    if canonical is None:
        raise artist_lookup.LookupError("MusicBrainz returned an invalid artist identity.")
    payload = payload | {"id": canonical}
    activity_store.store_artist_document(
        _key("musicbrainz", "artist", canonical, includes), payload
    )
    return activity_store.ArtistDocument(payload, datetime.now(UTC))


def _remember(payload: dict[str, Any]) -> dict[str, Any]:
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    activity_store.store_artist_document(digest, payload, review=True)
    return stored_review(digest, str(payload["artist_key"]))


def stored_review(token: str, artist_key: str) -> dict[str, Any]:
    stored = activity_store.artist_document(token, review=True)
    if stored is None:
        raise ReviewError("This review is unavailable. Fetch metadata and review again.")
    payload = stored.payload
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    if digest != token or payload.get("artist_key") != artist_key:
        raise ReviewError("This review does not match the selected artist. Review again.")
    return payload | {"token": token, "prepared_at": stored.fetched_at.isoformat()}


def prepare(
    artist: Artist,
    artist_id: str,
    language: str,
    music_dir: Path,
    info_dir: Path | None,
    selection: str | None,
    user_agent: str,
    *,
    refresh: bool = False,
) -> dict[str, Any]:
    if language not in LANGUAGES or mbid(artist_id) is None:
        raise ReviewError("Choose an artist identity and a supported biography language.")
    local = artist_nfo.for_artist(artist, music_dir, info_dir, selection, refresh=True)
    if local.profile is None and (local.candidates or selection):
        raise ReviewError("Choose the NFO source on the artist page first.")
    observed = _artist(artist_id, refresh=refresh)
    data = observed.payload
    canonical = str(data["id"])
    target = artist_nfo_edit.target(replace(artist, mbid=canonical), local.profile, info_dir)
    values = {
        tag: str(data.get(key) or "")
        for tag, key in (
            ("name", "name"),
            ("sortname", "sort-name"),
            ("type", "type"),
            ("gender", "gender"),
            ("disambiguation", "disambiguation"),
        )
        if data.get(key)
    }
    dates = data.get("life-span") or {}
    kind = str(data.get("type") or "").lower()
    if kind == "person" or kind in {"group", "orchestra", "choir"}:
        for key, tag in (
            ("begin", "born" if kind == "person" else "formed"),
            ("end", "died" if kind == "person" else "disbanded"),
        ):
            if dates.get(key):
                values[tag] = str(dates[key])
    tags = [str(row["name"]) for row in data.get("tag-list", []) if row.get("name")]
    if tags:
        values["genre"] = "\n".join(dict.fromkeys(tags))
    links = artist_lookup.wikipedia_links(data)
    biography = None
    biography_error = None
    biography_fetched_at = None
    try:
        entity = artist_lookup.wikidata_id(data)
        if entity:
            linked = _query(
                _key("wikidata", entity, "sitelinks", sorted(LANGUAGES)),
                lambda: artist_lookup.fetch_sitelinks(entity, user_agent),
                refresh=refresh,
            )
            links = {str(k): str(v) for k, v in linked.payload.items()} | links
        if language in links:
            article = _query(
                _key("wikipedia", language, links[language], "intro", "attribution-v1"),
                lambda: artist_lookup.fetch_biography(language, links[language], user_agent),
                refresh=refresh,
            )
            biography = article.payload
            biography_fetched_at = article.fetched_at.isoformat()
            # Validate provider metadata before it becomes reviewable evidence.
            artist_nfo.validate_source({k: str(v) for k, v in biography.items() if k != "text"})
            values["biography"] = str(biography["text"])
    except artist_lookup.LookupError as exc:
        # Partial profile data is labelled as such; biography is never an empty
        # replacement, and the user can retry without losing their local NFO.
        log.exception("Could not load the artist biography")
        biography_error = str(exc)
    current = dict(local.profile.edit_values) if local.profile else {"name": artist.name}
    return _remember(
        {
            "artist_key": artist.key,
            "mbid": canonical,
            "name": str(data.get("name") or artist.name),
            "language": language,
            "languages": sorted(links),
            "values": values,
            "current": current,
            "expected": local.profile.fingerprint if local.profile else "",
            "selection": selection,
            "target": str(target.resolve()),
            "biography": biography,
            "biography_error": biography_error,
            "biography_fetched_at": biography_fetched_at,
            "fetched_at": observed.fetched_at.isoformat(),
            "discography": None,
        }
    )


def extend_discography(
    token: str,
    artist_key: str,
    offset: int,
    *,
    refresh: bool = False,
    fields: Sequence[str] = (),
    albums: Sequence[str] = (),
) -> dict[str, Any]:
    payload = stored_review(token, artist_key)
    if offset < 0 or offset % artist_lookup.PAGE_SIZE or offset > 100_000:
        raise ReviewError("Choose a valid discography page.")
    available = dict(payload.get("picked_albums", {})) | {
        row["id"]: row for row in (payload.get("discography") or {}).get("albums", [])
    }
    if not set(albums).issubset(available) or not set(fields).issubset(payload["values"]):
        raise ReviewError("The selection does not belong to this review.")
    payload["picked_albums"] = {key: available[key] for key in dict.fromkeys(albums)}
    payload["selected_fields"] = list(dict.fromkeys(fields))
    observed = _query(
        _key("musicbrainz", "discography", payload["mbid"], offset, artist_lookup.PAGE_SIZE),
        lambda: artist_lookup.fetch_discography(payload["mbid"], offset),
        refresh=refresh,
    )
    # Do not retain an absent browse as a reusable negative answer.
    payload.pop("token")
    payload.pop("prepared_at")
    payload["discography"] = observed.payload | {"fetched_at": observed.fetched_at.isoformat()}
    return _remember(payload)


def apply(
    artist: Artist,
    token: str,
    fields: Sequence[str],
    albums: Sequence[str],
    music_dir: Path,
    info_dir: Path | None,
    backup_dir: Path,
) -> artist_nfo_edit.Saved:
    payload = stored_review(token, artist.key)
    selected = set(fields)
    if not selected.issubset(payload["values"]):
        raise ReviewError("The field selection does not belong to this review.")
    local = artist_nfo.for_artist(artist, music_dir, info_dir, payload["selection"], refresh=True)
    path = artist_nfo_edit.target(replace(artist, mbid=payload["mbid"]), local.profile, info_dir)
    if str(path.resolve()) != payload["target"]:
        raise ReviewError("The NFO target changed. Review the source again.")
    available = dict(payload.get("picked_albums", {})) | {
        row["id"]: row for row in (payload.get("discography") or {}).get("albums", [])
    }
    if not set(albums).issubset(available):
        raise ReviewError("The discography selection does not belong to this reviewed page.")
    if not selected and not albums:
        raise ReviewError("Choose at least one field or discography entry to import.")
    source = payload["biography"]
    return artist_nfo_edit.save(
        artist,
        music_dir,
        info_dir,
        backup_dir,
        {key: str(payload["values"][key]) for key in selected},
        payload["expected"],
        payload["selection"],
        confirmed_mbid=payload["mbid"],
        biography_source={k: str(v) for k, v in source.items() if k != "text"}
        if source and "biography" in selected
        else None,
        discography=[
            artist_nfo.DiscographyAlbum(available[key]["title"], available[key]["year"], key)
            for key in dict.fromkeys(albums)
        ],
    )
