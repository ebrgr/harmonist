"""Explicit artist API calls; MusicBrainz shares the configured global client.

Wikimedia hosts are fixed, never supplied by a page or followed from an arbitrary
relationship URL. This module knows nothing about caches, NFOs or confirmations.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import unquote, urlsplit

import httpx
import musicbrainzngs

from . import mb_lookup
from .artists import mbid

ARTIST_INCLUDES = ("aliases", "tags", "url-rels")
LANGUAGES = {"en": "English", "pt": "Português", "de": "Deutsch", "it": "Italiano"}
PAGE_SIZE = 20
_MAX_BYTES = 2 * 1024 * 1024


class LookupError(mb_lookup.MBError):
    """A provider failed; never an empty metadata response."""


def fetch_artist(artist_id: str) -> dict[str, Any]:
    try:
        result = musicbrainzngs.get_artist_by_id(artist_id, includes=list(ARTIST_INCLUDES))
    except musicbrainzngs.ResponseError as exc:
        if mb_lookup._is_not_found(exc):
            raise LookupError(
                "This artist is no longer on MusicBrainz. Choose an identity again."
            ) from exc
        raise LookupError(f"MusicBrainz could not load the artist: {exc}") from exc
    except (musicbrainzngs.NetworkError, musicbrainzngs.AuthenticationError) as exc:
        raise LookupError(f"MusicBrainz could not load the artist: {exc}") from exc
    artist = result.get("artist")
    if not isinstance(artist, dict) or not mbid(str(artist.get("id", ""))):
        raise LookupError("MusicBrainz returned an invalid artist response. Try again.")
    return dict(artist)


def search_artists(query: str) -> list[dict[str, str]]:
    try:
        result = musicbrainzngs.search_artists(query=query[:200], limit=20)
    except (musicbrainzngs.ResponseError, musicbrainzngs.NetworkError) as exc:
        raise LookupError(f"MusicBrainz artist search failed: {exc}") from exc
    rows = result.get("artist-list")
    if not isinstance(rows, list):
        raise LookupError("MusicBrainz returned an invalid search response. Try again.")
    return [
        {k: str(row.get(k) or "") for k in ("id", "name", "type", "country", "disambiguation")}
        for row in rows
        if isinstance(row, dict) and mbid(str(row.get("id", "")))
    ]


def fetch_discography(artist_id: str, offset: int) -> dict[str, Any]:
    try:
        result = musicbrainzngs.browse_release_groups(
            artist=artist_id, limit=PAGE_SIZE, offset=offset
        )
    except (musicbrainzngs.ResponseError, musicbrainzngs.NetworkError) as exc:
        raise LookupError(f"MusicBrainz discography lookup failed: {exc}") from exc
    rows, count = result.get("release-group-list"), result.get("release-group-count")
    if not isinstance(rows, list) or count is None:
        raise LookupError("MusicBrainz returned an incomplete discography response. Try again.")
    try:
        total = int(count)
    except (TypeError, ValueError) as exc:
        raise LookupError("MusicBrainz returned an invalid discography count.") from exc
    albums = []
    for row in rows:
        if not isinstance(row, dict) or not mbid(str(row.get("id", ""))):
            raise LookupError("MusicBrainz returned an invalid release group.")
        albums.append(
            {
                "id": str(row["id"]),
                "title": str(row.get("title") or ""),
                "year": str(row.get("first-release-date") or "")[:4],
            }
        )
    if total < offset + len(albums) or (offset < total and not albums):
        raise LookupError("MusicBrainz returned an inconsistent discography page. Try again.")
    return {"albums": albums, "total": total, "offset": offset}


def wikidata_id(artist: dict[str, Any]) -> str | None:
    links = {
        match.group(1)
        for rel in artist.get("url-relation-list", [])
        if rel.get("type") == "wikidata" and rel.get("ended") not in (True, "true", "1")
        if (
            match := re.fullmatch(
                r"https?://www\.wikidata\.org/wiki/(Q[1-9][0-9]*)", rel.get("target", "")
            )
        )
    }
    if len(links) > 1:
        raise LookupError(
            "MusicBrainz links multiple Wikidata identities. Correct the relationship before importing a biography."
        )
    return next(iter(links), None)


def wikipedia_links(artist: dict[str, Any]) -> dict[str, str]:
    links: dict[str, str] = {}
    for rel in artist.get("url-relation-list", []):
        if rel.get("type") != "wikipedia" or rel.get("ended") in (True, "true", "1"):
            continue
        url = urlsplit(rel.get("target", ""))
        for language in LANGUAGES:
            if (
                url.scheme in {"https", "http"}
                and url.hostname == f"{language}.wikipedia.org"
                and url.path.startswith("/wiki/")
            ):
                title = unquote(url.path.removeprefix("/wiki/"))
                if language in links and links[language] != title:
                    raise LookupError(
                        "MusicBrainz links multiple Wikipedia articles in this language."
                    )
                links[language] = title
    return links


def _api(host: str, params: dict[str, str], user_agent: str) -> dict[str, Any]:
    try:
        with httpx.stream(
            "GET",
            f"https://{host}/w/api.php",
            params=params | {"format": "json", "formatversion": "2"},
            headers={"User-Agent": user_agent},
            timeout=25,
        ) as response:
            response.raise_for_status()
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > _MAX_BYTES:
                    raise LookupError(f"{host} returned an oversized response.")
        payload = json.loads(data)
    except (httpx.HTTPError, ValueError) as exc:
        raise LookupError(f"Could not query {host}: {exc}") from exc
    if not isinstance(payload, dict) or "error" in payload:
        raise LookupError(f"{host} returned an API error. Try again.")
    return payload


def fetch_sitelinks(entity_id: str, user_agent: str) -> dict[str, str]:
    if not re.fullmatch(r"Q[1-9][0-9]*", entity_id):
        raise LookupError("Invalid Wikidata identity.")
    payload = _api(
        "www.wikidata.org",
        {"action": "wbgetentities", "ids": entity_id, "props": "sitelinks"},
        user_agent,
    )
    entities = payload.get("entities")
    entity = entities.get(entity_id) if isinstance(entities, dict) else None
    if (
        not isinstance(entity, dict)
        or "missing" in entity
        or not isinstance(entity.get("sitelinks"), dict)
    ):
        raise LookupError(
            "The linked Wikidata entity is unavailable. Check its identity and try again."
        )
    return {
        language: str(link["title"])
        for language in LANGUAGES
        if (link := entity["sitelinks"].get(f"{language}wiki")) and link.get("title")
    }


def fetch_biography(language: str, title: str, user_agent: str) -> dict[str, str]:
    if language not in LANGUAGES:
        raise LookupError("Choose a supported biography language.")
    payload = _api(
        f"{language}.wikipedia.org",
        {
            "action": "query",
            "titles": title,
            "redirects": "1",
            "prop": "extracts|info|revisions|pageprops",
            "exintro": "1",
            "explaintext": "1",
            "inprop": "url",
            "rvprop": "ids",
            "rvlimit": "1",
            "meta": "siteinfo",
            "siprop": "rightsinfo",
        },
        user_agent,
    )
    query = payload.get("query")
    if not isinstance(query, dict):
        raise LookupError("Wikipedia returned an invalid query response.")
    pages = query.get("pages")
    if (
        not isinstance(pages, list)
        or len(pages) != 1
        or not isinstance(pages[0], dict)
        or pages[0].get("missing")
        or pages[0].get("invalid")
    ):
        raise LookupError(
            "The linked Wikipedia article is unavailable. Refresh the identity links."
        )
    page, rights = pages[0], query.get("rightsinfo", {})
    if "disambiguation" in page.get("pageprops", {}):
        raise LookupError("The linked Wikipedia article is a disambiguation page.")
    if not page.get("extract"):
        raise LookupError("This Wikipedia article has no introductory biography to import.")
    if not isinstance(rights, dict) or not rights.get("url") or not rights.get("text"):
        raise LookupError("Wikipedia did not provide attribution information. Try again.")
    url = urlsplit(page.get("fullurl", ""))
    if url.scheme != "https" or url.hostname != f"{language}.wikipedia.org":
        raise LookupError("Wikipedia returned an invalid article URL.")
    return {
        "text": str(page["extract"]),
        "url": str(page["fullurl"]),
        "language": language,
        "title": str(page["title"]),
        "provider": "Wikipedia",
        "license": str(rights["text"]),
        "license_url": str(rights["url"]),
        "revision": str(page.get("revisions", [{}])[0].get("revid", "")),
    }
