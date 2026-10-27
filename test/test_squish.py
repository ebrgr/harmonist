"""Re-encoding an image to fit the image size limit (#888)."""

import io
import random

import pytest
from PIL import Image, ImageCms

from harmonist import images, squish


def _noise(size: int, *, seed: int = 1, mode: str = "RGB") -> Image.Image:
    """Grain, which is what JPEG compresses worst: the Mogwai cover's shape."""
    channels = len(mode)
    data = random.Random(seed).randbytes(size * size * channels)
    return Image.frombytes(mode, (size, size), data)


def _jpeg(image: Image.Image, **kwargs: object) -> bytes:
    buf = io.BytesIO()
    image.save(buf, "JPEG", quality=100, subsampling=0, **kwargs)
    return buf.getvalue()


def test_an_image_that_fits_is_left_alone():
    data = _jpeg(_noise(64))
    assert squish.fit(data, limit=len(data)) is None


def test_quality_alone_brings_it_under_and_keeps_every_pixel():
    data = _jpeg(_noise(256))
    # Between what quality 95 gives this grain (0.59 of it) and quality 92 (0.49).
    limit = len(data) * 55 // 100

    fitted = squish.fit(data, limit=limit)

    assert fitted is not None
    assert len(fitted) <= limit
    assert images.dimensions(fitted) == images.Size(256, 256)
    # The first step that fits, not the smallest: the least change is the point.
    first = squish.fit(data, limit=len(data) - 1)
    assert first is not None
    assert len(first) > len(fitted)


def test_dimensions_shrink_only_once_quality_runs_out():
    data = _jpeg(_noise(256))

    fitted = squish.fit(data, limit=len(data) // 20)

    assert fitted is not None
    assert len(fitted) <= len(data) // 20
    size = images.dimensions(fitted)
    assert size is not None
    assert size.width < 256
    assert size.width == size.height


def test_the_same_image_squishes_to_the_same_bytes():
    data = _jpeg(_noise(128))
    assert squish.fit(data, limit=len(data) // 2) == squish.fit(data, limit=len(data) // 2)


def test_a_png_becomes_a_jpeg():
    buf = io.BytesIO()
    _noise(128, mode="RGBA").save(buf, "PNG")
    data = buf.getvalue()

    fitted = squish.fit(data, limit=len(data) // 2)

    assert fitted is not None
    assert fitted.startswith(b"\xff\xd8")
    assert images.dimensions(fitted) == images.Size(128, 128)


def test_the_colour_profile_survives():
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    data = _jpeg(_noise(128), icc_profile=icc)

    fitted = squish.fit(data, limit=len(data) // 2)

    assert fitted is not None
    assert Image.open(io.BytesIO(fitted)).info.get("icc_profile") == icc


def test_an_image_it_cannot_read_is_an_error_not_a_result():
    with pytest.raises(squish.SquishError):
        squish.fit(b"\xff\xd8 not really a jpeg" * 100, limit=10)
