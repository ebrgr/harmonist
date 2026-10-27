"""One destination is reviewed for manual edits and online imports."""

import tomllib

from bs4 import BeautifulSoup

from harmonist import artist_nfo, config
from test import test_artist_import_web, test_artist_web
from test.test_artist_import_web import token
from test.test_artist_web import ALICE, BOB, make_album
from test.test_web import _post_settings

artist_client = test_artist_web.artist_client
providers = test_artist_import_web.providers
CAROL = "66666666-6666-4666-8666-666666666666"


def add_tree(cfg):
    for i in (1, 2):
        make_album(
            cfg.paths.music_dir,
            f"Carol/Album{i}",
            f"77777777-7777-4777-8777-{i:012d}",
            ["Carol"],
            [CAROL],
        )


def choice(response, kind):
    body = BeautifulSoup(response.text, "html.parser")
    options = [n for n in body.select('input[name="destination"]') if kind in n.parent.get_text()]
    assert options, response.text
    return str(options[0]["value"])


def test_create_without_external_root_previews_audio_path_and_keeps_draft_on_stale_target(
    artist_client,
):
    client, cfg, _ = artist_client
    cfg.paths.artist_info_dir = None
    add_tree(cfg)
    page = client.get(f"/artist/{CAROL}")
    assert (
        not BeautifulSoup(page.text, "html.parser")
        .find("button", string="Edit")
        .has_attr("disabled")
    )
    editor = client.get(f"/artist/{CAROL}/nfo/edit")
    destination = choice(editor, "Audio folders")
    assert "Carol/artist.nfo" in editor.text
    # An unscanned or differently credited album invalidates the reviewed path.
    make_album(cfg.paths.music_dir, "Carol/Unexpected", ALICE, ["Other"], [BOB])
    stale = client.post(
        f"/artist/{CAROL}/nfo", data={"destination": destination, "genre": "Draft genre"}
    )
    assert "HX-Redirect" not in stale.headers
    assert "Draft genre" in stale.text and "destination" in stale.text
    assert not (cfg.paths.music_dir / "Carol/artist.nfo").exists()


def test_editor_can_override_preference_and_creates_exactly_one_source(artist_client):
    client, cfg, _ = artist_client
    add_tree(cfg)
    editor = client.get(f"/artist/{CAROL}/nfo/edit")
    destination = choice(editor, "Audio folders")
    saved = client.post(f"/artist/{CAROL}/nfo", data={"destination": destination, "genre": "Rock"})
    assert "HX-Redirect" in saved.headers, saved.text
    path = cfg.paths.music_dir / "Carol/artist.nfo"
    assert artist_nfo.read(path).genres == ("Rock",)
    assert not (cfg.paths.artist_info_dir / "Carol/artist.nfo").exists()


def test_single_album_needs_an_explicit_audio_selection(artist_client):
    client, cfg, _ = artist_client
    cfg.paths.artist_info_dir = None
    make_album(cfg.paths.music_dir, "Carol/Only", CAROL, ["Carol"], [CAROL])
    editor = client.get(f"/artist/{CAROL}/nfo/edit")
    body = BeautifulSoup(editor.text, "html.parser")
    radios = body.select('input[name="destination"]')
    assert len(radios) == 1 and not radios[0].has_attr("checked")
    missing = client.post(f"/artist/{CAROL}/nfo", data={"genre": "Jazz"})
    assert "HX-Redirect" not in missing.headers
    assert not (cfg.paths.music_dir / "Carol/artist.nfo").exists()
    selected = client.post(
        f"/artist/{CAROL}/nfo", data={"genre": "Jazz", "destination": str(radios[0]["value"])}
    )
    assert "HX-Redirect" in selected.headers


def test_import_reviews_both_paths_and_saves_the_selected_audio_target_offline(
    artist_client, providers
):
    client, cfg, _ = artist_client
    add_tree(cfg)
    reviewed = client.post(f"/artist/{CAROL}/import/lookup", data={"language": "en"})
    snapshot = token(reviewed)
    destination = choice(reviewed, "Audio folders")
    for provider in providers.values():
        provider.reset_mock()
        provider.side_effect = AssertionError("Save must use the reviewed snapshot")
    saved = client.post(
        f"/artist/{CAROL}/import/apply",
        data={"review": snapshot, "destination": destination, "fields": ["biography"]},
    )
    assert "HX-Redirect" in saved.headers, saved.text
    assert (cfg.paths.music_dir / "Carol/artist.nfo").exists()
    assert not (cfg.paths.artist_info_dir / "Carol/artist.nfo").exists()
    for provider in providers.values():
        provider.assert_not_called()


def test_settings_location_persists_applies_live_and_environment_wins(artist_client, monkeypatch):
    client, cfg, _ = artist_client
    saved = _post_settings(client, artist_nfo_location="audio_preferred")
    assert "Settings saved" in saved.text
    assert client.app.state.cfg.artist_nfo.location == "audio_preferred"
    data = tomllib.loads((cfg.paths.config_dir / "harmonist.toml").read_text())
    assert data["artist_nfo"]["location"] == "audio_preferred"
    monkeypatch.setenv("HARMONIST_CONFIG_DIR", str(cfg.paths.config_dir))
    assert config.load().artist_nfo.location == "audio_preferred"
    monkeypatch.setenv("HARMONIST_ARTIST_NFO_LOCATION", "info_preferred")
    assert config.load().artist_nfo.location == "info_preferred"
    invalid = _post_settings(client, artist_nfo_location="both")
    assert "Couldn't save" in invalid.text
    assert client.app.state.cfg.artist_nfo.location == "audio_preferred"


def test_identified_local_credit_can_import_in_tree_and_replay_without_a_second_write(
    artist_client, providers
):
    from harmonist import activity_store

    client, cfg, _ = artist_client
    cfg.paths.artist_info_dir = None
    make_album(cfg.paths.music_dir, "Carol/Only", CAROL, ["Carol"], [])
    rows = client.get("/artists").context["artists_view"]["rows"]
    local = next(a for a in rows if a.name == "Carol")
    reviewed = client.post(
        f"/artist/{local.key}/import/lookup", data={"chosen_mbid": CAROL, "language": "en"}
    )
    payload = {
        "review": token(reviewed),
        "destination": choice(reviewed, "Audio folders"),
        "fields": ["biography"],
    }
    for provider in providers.values():
        provider.reset_mock()
        provider.side_effect = AssertionError("No network on reviewed Save")
    saved = client.post(f"/artist/{local.key}/import/apply", data=payload)
    assert "HX-Redirect" in saved.headers, saved.text
    profile = artist_nfo.read(cfg.paths.music_dir / "Carol/artist.nfo")
    assert profile.mbid == CAROL and profile.credit == local.key
    events = activity_store.artist_history(local.key)
    repeated = client.post(f"/artist/{local.key}/import/apply", data=payload)
    assert "HX-Redirect" in repeated.headers, repeated.text
    assert activity_store.artist_history(local.key) == events
    for provider in providers.values():
        provider.assert_not_called()
