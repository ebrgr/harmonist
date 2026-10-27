"""Artists navigation visibility is an optional installation preference."""

import tomllib
from unittest.mock import Mock

import pytest
from bs4 import BeautifulSoup
from pydantic import ValidationError

from harmonist import artists, config
from test import test_artist_web
from test.test_artist_web import ALICE
from test.test_web import _post_settings

artist_client = test_artist_web.artist_client


def test_ui_default_is_disabled_and_environment_overrides_toml(tmp_path, monkeypatch):
    assert config.UiConfig().show_artists is False
    monkeypatch.setenv("HARMONIST_CONFIG_DIR", str(tmp_path))
    (tmp_path / "harmonist.toml").write_text("[ui]\nshow_artists = true\n")
    assert config.load().ui.show_artists is True
    monkeypatch.setenv("HARMONIST_UI_SHOW_ARTISTS", "false")
    assert config.load().ui.show_artists is False
    monkeypatch.setenv("HARMONIST_UI_SHOW_ARTISTS", "invalid")
    with pytest.raises(ValidationError):
        config.load()


def test_hidden_tab_skips_the_index_and_keeps_profiles_and_library_links(
    artist_client, monkeypatch
):
    client, cfg, network = artist_client
    cfg.ui.show_artists = False
    index = Mock(side_effect=AssertionError("Hidden Artists list must not be prepared"))
    with monkeypatch.context() as scoped:
        scoped.setattr(artists, "page_vars", index)
        page = client.get("/?tab=artists&limit=1&q=Alice")
    assert page.context["initial_tab"] == "library"
    body = BeautifulSoup(page.text, "html.parser")
    assert body.select_one('[data-tab="artists"]') is None
    assert body.select_one("#panel-artists") is None
    assert body.select_one(".library-artist-link") is not None
    detail = client.get(f"/artist/{ALICE}")
    assert detail.context["artist_back_label"] == "Back to library"
    assert "tab=library" in detail.context["artist_back_url"]
    assert client.get("/artists").status_code == 200
    network.assert_not_called()


def test_ui_checkbox_persists_both_values_and_legacy_forms_preserve_it(artist_client):
    client, cfg, _ = artist_client
    cfg.ui.show_artists = False
    initial = BeautifulSoup(client.get("/settings").text, "html.parser")
    assert initial.find("h2", string="User Interface") is not None
    assert not initial.select_one('input[name="show_artists"]').has_attr("checked")
    enabled = _post_settings(client, ui_settings="1", show_artists="true")
    assert enabled.headers["HX-Refresh"] == "true"
    assert client.app.state.cfg.ui.show_artists is True
    assert client.get("/status").json()["ui"]["show_artists"] is True
    page = BeautifulSoup(client.get("/?tab=artists").text, "html.parser")
    assert page.select_one('[data-tab="artists"]') is not None
    assert page.select_one("#panel-artists") is not None
    _post_settings(client)  # an older Settings page did not contain this section
    assert client.app.state.cfg.ui.show_artists is True
    disabled = _post_settings(client, ui_settings="1")
    assert disabled.headers["HX-Refresh"] == "true"
    assert client.app.state.cfg.ui.show_artists is False
    assert client.get("/status").json()["ui"]["show_artists"] is False
    document = tomllib.loads((cfg.paths.config_dir / "harmonist.toml").read_text())
    assert document["ui"]["show_artists"] is False
    assert document["bandcamp"]["download_format"] == "flac"
