"""Artist audio trees with an optional external NFO root, confined to demo."""

from test.test_artist_nfo_location_web import CAROL
from test.test_artist_web import make_album

from harmonist import demo
from harmonist.web.main import create_app

DAVE = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
_seed = demo.seed


def seed(music_dir, **kwargs):
    _seed(music_dir, **kwargs)
    for index, (artist, key) in enumerate(
        (
            ("Carol", CAROL),
            ("Dave", DAVE),
            ("Eve", "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"),
            ("Fran", "ffffffff-ffff-4fff-8fff-ffffffffffff"),
        )
    ):
        for number in (1, 2):
            release = f"77777777-7777-4777-8777-{index * 2 + number:012d}"
            make_album(music_dir, f"{artist}/Album{number}", release, [artist], [key])


demo.seed = seed
app = create_app()
app.state.cfg.paths.artist_info_dir = app.state.cfg.paths.music_dir / "ArtistInfo"
without_info = create_app()
without_info.state.cfg.paths.artist_info_dir = None
