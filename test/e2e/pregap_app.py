"""An absent pregap file with correct Picard totals, like the Hybrid rip."""

import os
from dataclasses import replace

from pytest import MonkeyPatch
from test import demo_recovery

from harmonist import demo, formats

assert os.environ.get("HARMONIST_DEMO_MODE") == "1"
patch = MonkeyPatch()
demo_recovery.install(patch)
ALBUM_ID = "demo-rel-electric-mayhem"
medium = demo.MB_RELEASES[ALBUM_ID]["medium-list"][0]
medium["track-list"] = medium["track-list"][:2]
medium["track-count"] = "2"
medium["track-list"][0]["length"] = "20000"
medium["pregap"] = {
    "id": "demo-rt-pregap",
    "position": "0",
    "number": "0",
    "length": "10000",
    "recording": {"id": "demo-rec-pregap", "title": "Hidden Prelude"},
}
# The fixtures' tiny sine files stand in for a 30-second combined rip.
first_id = medium["track-list"][0]["id"]
read_tags, read_scan = formats.read_tags, formats.read_scan_fields


def combined_tags(path):
    tags = read_tags(path)
    return replace(tags, duration_ms=30000) if tags.release_track_id == first_id else tags


def combined_scan(path):
    fields = read_scan(path)
    return fields._replace(duration_ms=30000) if fields.release_track_id == first_id else fields


patch.setattr(formats, "read_tags", combined_tags)
patch.setattr(formats, "read_scan_fields", combined_scan)
patch.setattr(
    demo,
    "LIBRARY",
    [
        spec | {"release_tracks": 3} if spec.get("file_mbid") == ALBUM_ID else spec
        for spec in demo.LIBRARY
    ],
)
from harmonist.web import main

app = main.app
