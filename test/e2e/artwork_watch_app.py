"""Public demo artwork, with a simulated new upload for the watch regression."""

from harmonist import artwork_updates, artwork_watch, cover_art
from harmonist.web.main import app

_listed = cover_art.fetch_listing
_new: list[str] = []


def fetch_listing(kind, mbid, *, client=None):
    found = _listed(kind, mbid, client=client)
    if kind == "release" and mbid == "demo-rel-barryjive":
        found += [
            cover_art.Candidate(
                image_id,
                f"https://coverartarchive.org/release/{mbid}/mouse.png",
                f"https://coverartarchive.org/release/{mbid}/mouse.png",
                ("front",),
                True,
            )
            for image_id in _new
        ]
    return found


cover_art.fetch_listing = fetch_listing


def upload(image_id: str):
    _new.append(image_id)
    artwork_watch.check("demo-rel-barryjive", fresh=True)
    artwork_watch.prepare(app.state.scan_runner.albums())
    artwork_updates.observe("demo-rel-barryjive")
    return {"uploaded": image_id}


app.add_api_route("/test/artwork-upload/{image_id}", upload, methods=["POST"])
