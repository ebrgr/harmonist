"""Public demo plus a mixed-codec, coverless album for combined-filter review."""

import os
import shutil
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pytest import MonkeyPatch

from harmonist import demo, formats
from harmonist.tagger import tagsets_for
from harmonist.transforms import TaggingChoices

assert os.environ.get("HARMONIST_DEMO_MODE") == "1"
patch = MonkeyPatch()
ALBUM_ID = "demo-rel-rural-juror"
materialise = demo._materialise
build_sidecar = demo._build_sidecar


def dated_sidecar(sc_spec, album_spec):
    sc = build_sidecar(sc_spec, album_spec)
    recent = album_spec["mbid"] in {ALBUM_ID, "demo-rel-blues-brothers"}
    return replace(sc, added_at=datetime.now(UTC) - timedelta(days=1 if recent else 30))


def mixed_formats(music_dir, spec):
    if spec["mbid"] != ALBUM_ID:
        materialise(music_dir, spec)
        return
    materialise(music_dir, spec | {"cover": None})
    folder = music_dir / demo._safe(spec["artist"]) / demo._safe(spec["album"])
    source = min(folder.glob("*.m4a"))
    target = source.with_suffix(".mp3")
    shutil.copy(Path(__file__).parents[1] / "fixtures" / "sine.mp3", target)
    tags = tagsets_for(demo.MB_RELEASES[ALBUM_ID], TaggingChoices())[0]
    formats.write_tags(target, tags, None)
    source.unlink()


patch.setattr(demo, "_materialise", mixed_formats)
patch.setattr(demo, "_build_sidecar", dated_sidecar)
from harmonist.web import main

app = main.app
