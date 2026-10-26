"""Artist routes consume local snapshots and NFOs without network enrichment."""

import shutil
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
from mutagen.mp4 import MP4

from harmonist import formats, mb_lookup, sidecar
from harmonist.config import Config, PathsConfig, load
from harmonist.formats.m4a import ATOM_ALBUM_ARTISTS, ATOM_MB_ALBUM_ARTIST_ID
from harmonist.formats.types import TagSet
from harmonist.models import Sidecar
from harmonist.web.main import create_app

ALICE = "11111111-1111-4111-8111-111111111111"
BOB = "22222222-2222-4222-8222-222222222222"
RELEASE = "33333333-3333-4333-8333-333333333333"
SOLO = "44444444-4444-4444-8444-444444444444"


def make_album(root, directory, release, names, ids, *, confirmed=True):
    folder = root / directory
    folder.mkdir(parents=True)
    path = folder / "01.m4a"
    shutil.copy(Path(__file__).parent / "fixtures" / "sine.m4a", path)
    formats.write_tags(
        path,
        TagSet(
            mb_album_id=release,
            album=directory,
            album_artist=" & ".join(names),
            album_artists=names,
            mb_album_artist_ids=ids,
            title="Song",
            artist="Guest",
            track_num=1,
            track_total=1,
            disc_num=1,
            disc_total=1,
        ),
        None,
    )
    if confirmed:
        sidecar.write(folder, Sidecar(mb_release_id=release))
    return folder


@pytest.fixture
def artist_client(tmp_path, monkeypatch):
    music = tmp_path / "music"
    info = tmp_path / "ArtistInfo"
    cfg = Config(
        paths=PathsConfig(music_dir=music, config_dir=tmp_path / "config", artist_info_dir=info)
    )
    cfg.paths.config_dir.mkdir()
    make_album(music, "Together", RELEASE, ["Alice", "Bob"], [ALICE, BOB])
    make_album(music, "Solo", SOLO, ["Alice"], [ALICE])
    make_album(
        music,
        "Inbox only",
        "55555555-5555-4555-8555-555555555555",
        ["Pending"],
        [SOLO],
        confirmed=False,
    )
    folder = info / "Different directory name"
    folder.mkdir(parents=True)
    (folder / "artist.nfo").write_text(
        f"<artist><name>Alice</name><musicBrainzArtistID>{ALICE}</musicBrainzArtistID>"
        "<biography>&lt;script&gt;biography&lt;/script&gt;</biography><born>1980</born></artist>"
    )
    network = Mock(side_effect=AssertionError("Artist browsing must stay offline"))
    monkeypatch.setattr(mb_lookup, "fetch_release", network)
    return TestClient(create_app(cfg), headers={"HX-Request": "true"}), cfg, network


def test_list_and_artist_page_are_offline_and_share_the_album_tiles(artist_client):
    client, _cfg, network = artist_client
    response = client.get("/?tab=artists")
    assert response.status_code == 200
    assert [a.name for a in response.context["artists_view"]["rows"]] == ["Alice", "Bob"]
    assert response.context["initial_tab"] == "artists"
    detail = client.get(f"/artist/{ALICE}?artist_q=Alice&artist_page=2")
    assert detail.status_code == 200
    assert {a.id for a in detail.context["rows"]} == {RELEASE, SOLO}
    assert "&lt;script&gt;biography&lt;/script&gt;" in detail.text
    assert "<script>biography</script>" not in detail.text
    assert f"https://musicbrainz.org/artist/{ALICE}" in detail.text
    assert f"/album/{RELEASE}?from_artist={ALICE}" in detail.text
    bob = client.get(f"/artist/{BOB}")
    assert {a.id for a in bob.context["rows"]} == {RELEASE}
    assert client.get("/artist/unknown").status_code == 404
    assert client.get(f"/artist/{BOB}/image").status_code == 404
    network.assert_not_called()


@pytest.mark.parametrize("path", ["/?tab=library", "/library"])
def test_library_cards_link_each_credit_separately_from_the_album(artist_client, path):
    client, _cfg, network = artist_client
    body = BeautifulSoup(client.get(path).text, "html.parser")
    album_link = body.select_one(f"a#lib-{RELEASE}")
    card = album_link.find_parent(class_="lib-tile")
    assert card is not None
    assert album_link.select_one("img") is not None
    assert "Together" in album_link.get_text()
    names = card.select(".library-artist-link")
    assert [a.get_text() for a in names] == ["Alice", "Bob"]
    assert [urlsplit(str(a["href"])).path for a in names] == [f"/artist/{ALICE}", f"/artist/{BOB}"]
    assert all(a.find_parent("a") is None for a in names)
    for link, expected in zip(names, [{RELEASE, SOLO}, {RELEASE}], strict=True):
        assert {a.id for a in client.get(link["href"]).context["rows"]} == expected
    network.assert_not_called()


def test_library_artist_links_keep_homonyms_and_unidentified_credits_scoped(artist_client):
    client, cfg, network = artist_client
    make_album(cfg.paths.music_dir, "Homonym", ALICE, ["Alice"], [SOLO])
    make_album(cfg.paths.music_dir, "Unidentified", BOB, ["Alice"], [])
    make_album(
        cfg.paths.music_dir,
        "Another unidentified",
        "77777777-7777-4777-8777-777777777777",
        ["Alice"],
        [],
    )
    page = client.get("/library")
    body = BeautifulSoup(page.text, "html.parser")
    by_title = {a.title: a for a in page.context["rows"]}
    rows = client.get("/artists").context["artists_view"]["rows"]
    local = {a.albums[0].title: a.key for a in rows if a.mbid is None}
    assert len(set(local.values())) == 2
    for title, key in [("Homonym", SOLO), *local.items()]:
        card = body.select_one(f"#lib-{by_title[title].id}").find_parent(class_="lib-tile")
        link = card.select_one(".library-artist-link")
        assert link is not None
        assert link.get_text() == "Alice"
        assert urlsplit(str(link["href"])).path == f"/artist/{key}"
        detail = client.get(str(link["href"]))
        assert detail.context["artist"].key == key
        assert [a.title for a in detail.context["rows"]] == [title]
    network.assert_not_called()


def test_library_artist_album_round_trip_preserves_navigation_and_nfo_selection(artist_client):
    client, _cfg, network = artist_client
    origin = "/?tab=library&page=2&limit=1&filter=codec-alac&q=Alice"
    library = BeautifulSoup(client.get(origin).text, "html.parser")
    artist_link = library.select_one(".library-artist-link")
    assert artist_link is not None
    detail = client.get(str(artist_link["href"]))
    assert detail.context["artist_back_url"] == origin
    assert "← Back to library" in detail.text
    next_page = client.get(detail.context["artist_next"])
    assert next_page.context["page"] == 2
    assert next_page.context["artist_back_url"] == origin
    # Source selection, local refresh and editor actions retain the same origin.
    selected = next_page.context["nfo_choices"][0][1]
    selected_page = client.get(selected)
    for key in ("artist_reload_url", "artist_edit_url", "artist_import_url"):
        params = parse_qs(urlsplit(selected_page.context[key]).query)
        assert params["library_page"] == ["2"]
        assert params["library_limit"] == ["1"]
        assert params["library_filter"] == ["codec-alac"]
        assert params["library_q"] == ["Alice"]
        assert "nfo" in params
    editor = client.get(selected_page.context["artist_edit_url"])
    assert "library_page=2" in editor.context["nfo_post_url"]
    body = BeautifulSoup(selected_page.text, "html.parser")
    album_link = body.select_one(".lib-tile a[id^='lib-']")
    assert album_link is not None
    album = client.get(str(album_link["href"]))
    back = album.context["artist_back_url"]
    assert "from_nfo" in parse_qs(urlsplit(str(album_link["href"])).query)
    returned = client.get(back)
    assert returned.context["page"] == 2
    assert returned.context["nfo"].profile.path == selected_page.context["nfo"].profile.path
    assert returned.context["artist_back_url"] == origin
    final = client.get(returned.context["artist_back_url"])
    assert final.context["page"] == 2
    network.assert_not_called()


def test_artist_library_return_is_local_and_escapes_search_text(artist_client):
    client, _cfg, _network = artist_client
    query = 'Alice & Bob # " <x>'
    response = client.get(
        f"/artist/{ALICE}",
        params={
            "library_page": "3",
            "library_limit": "60",
            "library_filter": "codec-aac,evil",
            "library_q": query,
        },
    )
    url = urlsplit(response.context["artist_back_url"])
    assert url.scheme == url.netloc == "" and url.path == "/"
    assert parse_qs(url.query) == {
        "tab": ["library"],
        "page": ["3"],
        "limit": ["60"],
        "filter": ["codec-aac"],
        "q": [query],
    }
    invalid = client.get(f"/artist/{ALICE}?library_page=https://example.com")
    assert invalid.context["artist_back_url"].startswith("/?tab=artists")


def test_search_refresh_and_nfo_errors_recover_without_hiding_albums(artist_client):
    client, cfg, network = artist_client
    found = client.get(
        "/artists?artist_q=bOb&artist_page=999", headers={"X-Harmonist-Push-Url": "1"}
    )
    assert [a.name for a in found.context["artists_view"]["rows"]] == ["Bob"]
    assert found.context["artists_view"]["page"] == 1
    assert "artist_q=bOb" in found.headers["HX-Push-Url"]
    path = cfg.paths.artist_info_dir / "Different directory name" / "artist.nfo"
    original = path.read_bytes()
    path.write_text("<artist>")
    broken = client.get(f"/artist/{ALICE}")
    assert broken.context["nfo"].errors
    assert len(broken.context["rows"]) == 2
    path.write_bytes(original)
    assert client.get(f"/artist/{ALICE}").context["nfo"].profile is not None
    network.assert_not_called()


def test_missing_information_folder_still_serves_local_metadata_and_artwork(artist_client):
    client, cfg, network = artist_client
    source = cfg.paths.artist_info_dir / "Different directory name" / "artist.nfo"
    local = cfg.paths.music_dir / "artist.nfo"
    local.write_bytes(source.read_bytes())
    image = cfg.paths.music_dir / "artist.png"
    image.write_bytes(b"local-artist-image")
    shutil.rmtree(cfg.paths.artist_info_dir)
    before = {p: p.read_bytes() for p in cfg.paths.music_dir.rglob("*") if p.is_file()}
    detail = client.get(f"/artist/{ALICE}")
    assert detail.status_code == 200
    assert detail.context["nfo"].profile is not None
    assert detail.context["nfo"].profile.path == local
    assert str(cfg.paths.artist_info_dir) in detail.text
    assert "&lt;script&gt;biography&lt;/script&gt;" in detail.text
    assert {a.id for a in detail.context["rows"]} == {RELEASE, SOLO}
    artwork = client.get(f"/artist/{ALICE}/image")
    assert artwork.status_code == 200
    assert artwork.content == image.read_bytes()
    assert {p: p.read_bytes() for p in before} == before
    network.assert_not_called()


def test_artist_information_folder_environment_and_demo_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv("HARMONIST_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("HARMONIST_MUSIC_DIR", str(tmp_path / "music"))
    monkeypatch.setenv("HARMONIST_ARTIST_INFO_DIR", str(tmp_path / "Artists"))
    monkeypatch.setenv("HARMONIST_DEMO_MODE", "0")
    assert load().paths.artist_info_dir == tmp_path / "Artists"
    monkeypatch.setenv("HARMONIST_DEMO_MODE", "1")
    assert load().paths.artist_info_dir is None


def add_unidentified_tracks(folder):
    for i in (2, 3):
        path = folder / f"{i:02d}.m4a"
        shutil.copy(folder / "01.m4a", path)
        audio = MP4(path)
        del audio[ATOM_ALBUM_ARTISTS]
        del audio[ATOM_MB_ALBUM_ARTIST_ID]
        audio.save()


def test_tracks_without_artist_ids_do_not_split_an_identified_artist(artist_client):
    client, cfg, network = artist_client
    add_unidentified_tracks(cfg.paths.music_dir / "Solo")
    before = {p: p.read_bytes() for p in cfg.paths.music_dir.rglob("*") if p.is_file()}
    response = client.get("/artists?artist_q=Alice")
    rows = response.context["artists_view"]["rows"]
    assert [(a.key, len(a.albums)) for a in rows] == [(ALICE, 2)]
    assert [a.title for a in rows[0].unidentified_albums] == ["Solo"]
    assert "Unidentified credit - Solo" in BeautifulSoup(response.text, "html.parser").get_text()
    detail = client.get(f"/artist/{ALICE}")
    assert {a.id for a in detail.context["rows"]} == {RELEASE, SOLO}
    assert "Unidentified credit - Solo" in BeautifulSoup(detail.text, "html.parser").get_text()
    assert {p: p.read_bytes() for p in before} == before
    network.assert_not_called()


def test_artist_sections_and_discography_do_not_change_owned_album_count(artist_client):
    client, cfg, network = artist_client
    path = cfg.paths.artist_info_dir / "Different directory name" / "artist.nfo"
    path.write_text(
        path.read_text().replace(
            "</artist>",
            "<genre>Rock</genre><style>Alternative</style>"
            "<album><title>Not in library</title><year>1999</year></album></artist>",
        )
    )
    response = client.get(f"/artist/{ALICE}")
    body = BeautifulSoup(response.text, "html.parser")
    assert body.select_one("#artist-biography p").get_text() == "<script>biography</script>"
    assert body.select_one("#artist-biography").get("open") is None
    assert body.select_one("#artist-discography").get("open") is None
    assert "Not in library" in body.select_one("#artist-discography").get_text()
    assert len(body.select("#library-grid .lib-tile")) == 2
    assert body.select_one('[aria-label="Artist genres"]').get_text(strip=True) == "Rock"
    assert (
        "Alternative" in body.select_one('[aria-labelledby="artist-additional-heading"]').get_text()
    )
    network.assert_not_called()


def test_edit_and_create_nfo_preserve_audio_and_show_saved_genres(artist_client):
    client, cfg, network = artist_client
    before = {p: p.read_bytes() for p in cfg.paths.music_dir.rglob("*") if p.is_file()}
    editor = client.get(f"/artist/{ALICE}/nfo/edit?artist_q=Alice&artist_page=2")
    assert editor.status_code == 200
    token = BeautifulSoup(editor.text, "html.parser").select_one('input[name="expected"]')
    assert token is not None
    expected = str(token["value"])
    saved = client.post(
        f"/artist/{ALICE}/nfo?artist_q=Alice&artist_page=2",
        data={
            "expected": expected,
            "genre": ["Rock", "Alternative"],
            "biography": "Edited biography",
        },
    )
    assert saved.status_code == 200
    assert "artist_q=Alice" in saved.headers["HX-Redirect"]
    page = client.get(saved.headers["HX-Redirect"])
    assert "Edited biography" in page.text
    assert page.context["nfo"].profile.genres == ("Rock", "Alternative")
    assert len(list((cfg.paths.config_dir / "nfoartists").glob("*.nfo"))) == 1
    created = client.post(
        f"/artist/{BOB}/nfo", data={"expected": "", "name": "Bob", "genre": "Jazz"}
    )
    assert created.status_code == 200
    profile = client.get(created.headers["HX-Redirect"]).context["nfo"].profile
    assert profile.mbid == BOB and profile.genres == ("Jazz",)
    assert profile.path == cfg.paths.artist_info_dir / BOB / "artist.nfo"
    assert {p: p.read_bytes() for p in before} == before
    network.assert_not_called()


def test_stale_nfo_editor_keeps_draft_and_requires_rereading(artist_client):
    client, cfg, _network = artist_client
    editor = client.get(f"/artist/{ALICE}/nfo/edit")
    token = BeautifulSoup(editor.text, "html.parser").select_one('input[name="expected"]')
    assert token is not None
    expected = str(token["value"])
    path = cfg.paths.artist_info_dir / "Different directory name" / "artist.nfo"
    path.write_text(path.read_text().replace("1980", "1981"))
    original = path.read_bytes()
    response = client.post(
        f"/artist/{ALICE}/nfo", data={"expected": expected, "born": "1990", "genre": "Rock"}
    )
    assert "HX-Redirect" not in response.headers
    assert "changed since" in response.text
    assert 'value="1990"' in response.text
    assert "Read again" in response.text
    assert path.read_bytes() == original


def test_nfo_editor_can_clear_fields_without_clearing_omitted_fields(artist_client):
    client, cfg, _network = artist_client
    path = cfg.paths.artist_info_dir / "Different directory name" / "artist.nfo"
    token = BeautifulSoup(client.get(f"/artist/{ALICE}/nfo/edit").text, "html.parser").select_one(
        'input[name="expected"]'
    )
    assert token is not None
    saved = client.post(
        f"/artist/{ALICE}/nfo", data={"expected": str(token["value"]), "biography": "", "genre": ""}
    )
    profile = client.get(saved.headers["HX-Redirect"]).context["nfo"].profile
    assert profile.biography == ""
    assert profile.genres == ()
    assert profile.name == "Alice"
    assert ("Born", "1980") in profile.facts
    assert "biography" not in path.read_text()
