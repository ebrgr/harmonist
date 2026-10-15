"""An absent pregap file with correct Picard totals, like the Hybrid rip."""

import os

from pytest import MonkeyPatch
from test import demo_recovery

from harmonist import demo

assert os.environ.get("HARMONIST_DEMO_MODE") == "1"
patch = MonkeyPatch()
demo_recovery.install(patch)
ALBUM_ID = "demo-rel-electric-mayhem"
medium = demo.MB_RELEASES[ALBUM_ID]["medium-list"][0]
medium["track-list"] = medium["track-list"][:2]
medium["track-count"] = "2"
medium["pregap"] = {
    "id": "demo-rt-pregap",
    "position": "0",
    "number": "0",
    "length": "186000",
    "recording": {"id": "demo-rec-pregap", "title": "Hidden Prelude"},
}
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
