"""Sibling and unrelated releases for the album-page picker."""

import os
from copy import deepcopy

from harmonist import demo
from harmonist.web.main import create_app

original = demo.MB_RELEASES["demo-rel-dingoes"]
sibling = deepcopy(original)
sibling["id"] = "demo-rel-picker-sibling"
sibling["disambiguation"] = "Alternate pressing"
sibling["medium-list"][0]["format"] = "CD"
if os.environ.get("HARMONIST_TEST_NO_SIBLINGS") != "1":
    demo.MB_RELEASES[sibling["id"]] = sibling

unrelated = deepcopy(original)
unrelated["id"] = "demo-rel-picker-other"
unrelated["title"] = "Another release"
unrelated["release-group"]["id"] = "demo-rg-picker-other"
medium = unrelated["medium-list"][0]
bonus = deepcopy(medium["track-list"][-1])
bonus["id"] = "demo-rt-picker-bonus"
bonus["position"] = "4"
bonus["number"] = "4"
bonus["recording"]["id"] = "demo-rec-picker-bonus"
bonus["recording"]["title"] = "Bonus track"
medium["track-list"].append(bonus)
medium["track-count"] = 4
demo.MB_RELEASES[unrelated["id"]] = unrelated

app = create_app()
