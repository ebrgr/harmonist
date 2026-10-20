"""Public demo with two more albums whose date has drifted, so the Inbox has
several Enrichment updates to select and apply together (#814)."""

import os

from pytest import MonkeyPatch

from harmonist import demo

assert os.environ.get("HARMONIST_DEMO_MODE") == "1"
patch = MonkeyPatch()
DRIFTED = {"demo-rel-barryjive", "demo-rel-sex-bob-omb"}
materialise = demo._materialise


def drifted(music_dir, spec):
    if spec["mbid"] in DRIFTED:
        spec = spec | {"tags": {**spec.get("tags", {}), "date": "1999"}}
    materialise(music_dir, spec)


patch.setattr(demo, "_materialise", drifted)
from harmonist.web import main

app = main.app
