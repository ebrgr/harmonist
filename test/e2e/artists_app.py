"""A collaboration in an arbitrary layout with separate artist information."""

from pathlib import Path

from test.test_artist_web import ALICE, BOB, RELEASE, SOLO, add_unidentified_tracks, make_album

from harmonist import demo
from harmonist.web.main import create_app

_seed = demo.seed


def seed(music_dir, **kwargs):
    _seed(music_dir, **kwargs)
    make_album(music_dir, "Unstructured collaboration", RELEASE, ["Álice", "Zed"], [ALICE, BOB])
    solo = make_album(music_dir, "Another arbitrary folder", SOLO, ["Álice"], [ALICE])
    add_unidentified_tracks(solo)
    make_album(music_dir, "Unidentified artist profile", RELEASE, ["Unknown fixture artist"], [])
    # Exercise the second Artists page as well as the search/back path.
    for i in range(31):
        release = f"99999999-9999-4999-8999-{i:012d}"
        artist = f"88888888-8888-4888-8888-{i:012d}"
        name = f"ZZ artist {i:02d}"
        if i == 0:
            name += " with a long collaborative artist credit" * 4
        make_album(music_dir, f"ZZ fixture {i:02d}", release, [name], [artist])
    info = music_dir / "ArtistInfo" / "unrelated directory name"
    info.mkdir(parents=True)
    (info / "artist.nfo").write_text(
        f"<artist><name>Álice</name><musicbrainzartistid>{ALICE}</musicbrainzartistid>"
        "<biography>A locally stored biography. "
        + ("A long paragraph about the artist. " * 120)
        + "</biography>"
        "<formed>2001</formed><genre>Rock</genre><style>Alternative</style>"
        "<album><title>NFO discography album</title><year>1999</year></album>"
        f"<album><title>Another discography album</title><year>2000</year><musicbrainzreleasegroupid>{ALICE}</musicbrainzreleasegroupid></album>"
        "</artist>"
    )
    # An existing demo asset, not a downloaded image.
    (info / "artist.png").write_bytes((Path(demo.ASSETS_DIR) / "wyld.png").read_bytes())


demo.seed = seed
app = create_app()
app.state.cfg.paths.artist_info_dir = app.state.cfg.paths.music_dir / "ArtistInfo"
