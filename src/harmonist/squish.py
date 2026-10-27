"""Re-encoding an image so it fits the image size limit (#888).

An image over the limit is one a player won't read: Navidrome refuses anything
over its `MaxImageSize` and keeps whatever art it held before. The archive's
originals are sometimes far over it, saved at quality 100 by whoever scanned
them, so an image picked by size or by hand is squished as it is written rather
than refused.

Squishing does the least that fits. Quality first, at the same pixel
dimensions, because a quality-100 scan loses next to nothing at 95: the
34 MB Mogwai cover came out at 16 MB with an SSIM of 0.990. Keeping the
dimensions also keeps the size rule settled, since a tie goes to the image
already there and the original never counts as larger than its copy. The
dimensions shrink only once quality has run out.

Always JPEG out, whatever came in: a PNG over the limit can only shrink by
losing its losslessness. The colour profile and EXIF go with it.

The only module that uses Pillow. `images` reads dimensions without it, and
that's still all most of Harmonist needs.
"""

from __future__ import annotations

import io

from PIL import Image, UnidentifiedImageError

#: Tried in order at the image's own dimensions, full colour (4:4:4).
_QUALITIES = (95, 92, 90, 85)
#: Below the qualities, the dimensions shrink by this much a step, at the last.
_SHRINK = 0.8
#: Modes a JPEG holds as they are; anything else (alpha, palette) becomes RGB.
_JPEG_MODES = frozenset({"RGB", "L", "CMYK"})


class SquishError(Exception):
    """The image couldn't be decoded, so it couldn't be squished. Distinct
    from "it already fits", which is `fit` returning None."""


def fit(data: bytes, *, limit: int) -> bytes | None:
    """`data` re-encoded as a JPEG of at most `limit` bytes, or None when it
    already fits and nothing needs writing differently.

    Deterministic: the same image and limit give the same bytes, with the same
    Pillow. Raises `SquishError` for an image Pillow can't decode."""
    if len(data) <= limit:
        return None
    image: Image.Image
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as e:
        raise SquishError(str(e)) from e
    extras = {
        key: value
        for key in ("icc_profile", "exif")
        if isinstance(value := image.info.get(key), bytes)
    }
    if image.mode not in _JPEG_MODES:
        image = image.convert("RGB")
    for quality in _QUALITIES:
        out = _encode(image, quality, extras)
        if len(out) <= limit:
            return out
    while True:
        width, height = image.size
        image = image.resize(
            (max(1, round(width * _SHRINK)), max(1, round(height * _SHRINK))),
            Image.Resampling.LANCZOS,
        )
        out = _encode(image, _QUALITIES[-1], extras)
        if len(out) <= limit or image.size == (1, 1):
            return out


def _encode(image: Image.Image, quality: int, extras: dict[str, bytes]) -> bytes:
    # No `optimize`: it makes Pillow encode into one buffer sized at a guess of
    # a byte or two per pixel, which grain at a high quality outgrows, and the
    # save fails. Streaming has no such limit, and optimized Huffman tables save
    # only a few percent.
    buf = io.BytesIO()
    image.save(buf, "JPEG", quality=quality, subsampling=0, **extras)
    return buf.getvalue()
