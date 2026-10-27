"""Artwork eligibility decisions; proposals use the ordinary artwork plan (#586)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from . import artwork, caa_cache, cover_art, derived, formats, mb_cache, mb_lookup, sidecar
from .models import Album, ArtworkWatch, Sidecar


@dataclass(frozen=True)
class Image:
    candidate: cover_art.Candidate
    image: formats.EmbeddedArt | None


_observed: dict[str, tuple[Image, ...]] = {}


def reset() -> None:
    _observed.clear()


def active(sc: Sidecar | None) -> ArtworkWatch | None:
    watch = sc.artwork_watch if sc else None
    return watch if watch and sc and watch.release == sc.mb_release_id else None


def sources(mbid: str) -> tuple[tuple[str, str], ...]:
    release = mb_cache.stored_release(mbid)
    group = (release.get("release-group") or {}).get("id") if release else None
    return (("release", mbid),) + ((("release-group", group),) if isinstance(group, str) else ())


def due(mbid: str) -> bool:
    scope = sources(mbid)
    return len(scope) < 2 or any(
        caa_cache.stored_listing(kind, key, fresh=True) is None for kind, key in scope
    )


def check(mbid: str, *, fresh: bool = False) -> None:
    if len(sources(mbid)) < 2:
        release = mb_cache.fetch_release(mbid, max_age=mb_cache.FRESH)
        if release.get("id") != mbid or len(sources(mbid)) < 2:
            raise mb_lookup.MBError("The watched release or its group changed; refresh its match.")
    for kind, key in sources(mbid):
        caa_cache.listing(kind, key, max_age=caa_cache.FRESH if fresh else None)


def _listed(mbid: str) -> tuple[cover_art.Candidate, ...]:
    found: dict[str, cover_art.Candidate] = {}
    for kind, key in sources(mbid):
        for candidate in caa_cache.stored_listing(kind, key) or ():
            found.setdefault(candidate.image_id, candidate)
    return tuple(found.values())


def prepare(albums: Sequence[Album]) -> None:
    """Fetch only as far as the first eligible alternative for each copy.

    Explicit watches allow smaller originals. Cached uploads are shared; no
    sibling browse, measurements, or music writes are needed here.
    """
    for album in albums:
        watch = active(album.sidecar)
        local = album.artwork_local
        if watch is None or album.sidecar is None or local is None or local.unreadable:
            continue
        carried = {a.digest for _, a in local.tracks if a}
        if local.cover:
            carried.add(local.cover.image.digest)
        # A squished copy of an image is that image (#888).
        carried = derived.carried(carried)
        for candidate in _listed(watch.release):
            if not candidate.front or candidate.image_id in album.sidecar.artwork_rejected:
                continue
            original = cover_art.candidate_image(candidate)
            image = formats.EmbeddedArt.of(original.data, original.mime)
            if image.digest not in carried:
                break


def observe(mbid: str) -> bool:
    """Warm descriptions for pure Library filtering; no network."""
    answer = caa_cache.stored(mbid)
    result = []
    for candidate in _listed(mbid):
        original = cover_art.cached_candidate_image(candidate.image_id)
        if original is None and answer and answer.image_url == candidate.image_url:
            original = cover_art.cached_front(mbid)
        result.append(
            Image(
                candidate,
                formats.EmbeddedArt.of(original.data, original.mime) if original else None,
            )
        )
    observed = tuple(result)
    previous = _observed.get(mbid)
    _observed[mbid] = observed
    return observed != previous


def excluded(sc: Sidecar | None, image: formats.EmbeddedArt | None) -> bool:
    return bool(
        sc
        and image
        and any(
            i.candidate.image_id in sc.artwork_rejected
            and i.image
            and i.image.digest == image.digest
            for i in _observed.get(sc.mb_release_id or "", ())
        )
    )


def proposal(
    sc: Sidecar | None,
    tracks: Sequence[tuple[Path, formats.EmbeddedArt | None]],
    cover: artwork.FolderCover | None,
    folder_cover: artwork.FolderCoverPolicy,
) -> tuple[dict[str, str], dict[str, formats.EmbeddedArt]]:
    """A review proposal, never permission for an unattended write."""
    watch = active(sc)
    if watch is None or sc is None:
        return {}, {}
    carried = {a.digest for _, a in tracks if a}
    if cover:
        carried.add(cover.image.digest)
    # A squished copy of an image is that image (#888).
    carried = derived.carried(carried)
    for item in _observed.get(watch.release, ()):
        image = item.image
        candidate = item.candidate
        if (
            not candidate.front
            or candidate.image_id in sc.artwork_rejected
            or image is None
            or image.digest in carried
        ):
            continue
        choices = (
            {}
            if artwork.has_per_track_art(a.digest if a else None for _, a in tracks)
            else {a.digest if a else artwork.GAP: candidate.image_id for _, a in tracks}
        )
        if cover is not None or folder_cover is artwork.FolderCoverPolicy.IF_MISSING:
            choices[artwork.COVER] = candidate.image_id
        return choices, {candidate.image_id: image}
    return {}, {}


def save(album: Album, watch: ArtworkWatch | None) -> bool:
    changed = False
    mbid = album.sidecar.mb_release_id if album.sidecar else None
    for folder in album.folders or [album.path]:
        sc = sidecar.read(folder)
        if sc is None or sc.mb_release_id != mbid:
            raise ValueError(
                "The album's release changed; reload before changing its artwork watch."
            )
        if sc.artwork_watch != watch:
            sidecar.write(folder, replace(sc, artwork_watch=watch))
            changed = True
    return changed


def reject(album: Album, image_id: str, rejected: bool) -> bool:
    changed = False
    for folder in album.folders or [album.path]:
        sc = sidecar.read(folder)
        if sc is None:
            raise ValueError("The album changed; reload before changing its artwork choices.")
        ids = (
            tuple(dict.fromkeys((*sc.artwork_rejected, image_id)))
            if rejected
            else tuple(i for i in sc.artwork_rejected if i != image_id)
        )
        if ids != sc.artwork_rejected:
            sidecar.write(folder, replace(sc, artwork_rejected=ids))
            changed = True
    return changed
