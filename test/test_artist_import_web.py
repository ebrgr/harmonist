"""Artist imports review one immutable answer before touching a local NFO."""

import hashlib
from unittest.mock import Mock

import pytest
from bs4 import BeautifulSoup

from harmonist import activity_store, artist_lookup, artist_nfo, demo
from test import test_artist_web
from test.test_artist_web import ALICE, BOB, RELEASE, make_album

artist_client = test_artist_web.artist_client


def test_import_review_opens_offline_and_offers_biography_languages(artist_client, providers):
    client, _cfg, network = artist_client
    response = client.get(f"/artist/{ALICE}/import")
    assert response.status_code == 200
    assert 'name="language"' in response.text
    for language in ("en", "pt", "de", "it"):
        assert f'value="{language}"' in response.text
    network.assert_not_called()
    for provider in providers.values():
        provider.assert_not_called()


@pytest.fixture
def providers(monkeypatch):
    mocks = {}
    for name, fake in (
        ("fetch_artist", demo.artist_metadata),
        ("search_artists", demo.artist_search),
        ("fetch_sitelinks", demo.artist_sitelinks),
        ("fetch_biography", demo.artist_biography),
        ("fetch_discography", demo.artist_discography),
    ):
        mocks[name] = Mock(side_effect=fake)
        monkeypatch.setattr(artist_lookup, name, mocks[name])
    return mocks


def token(response):
    assert response.status_code == 200
    node = BeautifulSoup(response.text, "html.parser").select_one('input[name="review"]')
    assert node is not None, response.text
    return str(node["value"])


def source_file(cfg):
    return cfg.paths.artist_info_dir / "Different directory name" / "artist.nfo"


def test_selected_biography_saves_exact_review_offline_with_attribution_backup_and_undo(
    artist_client, providers
):
    client, cfg, _network = artist_client
    path = source_file(cfg)
    original = path.read_bytes()
    audio = {p: p.read_bytes() for p in cfg.paths.music_dir.rglob("*") if p.is_file()}
    response = client.post(f"/artist/{ALICE}/import/lookup", data={"language": "pt"})
    reviewed = token(response)
    assert (
        response.context["import_review"]["values"]["biography"]
        == demo.artist_biography("pt", "Demo artist", "")["text"]
    )
    # Another tab changes every online value; the first review must remain usable.
    providers["fetch_artist"].side_effect = lambda id: (
        demo.artist_metadata(id) | {"name": "Changed online"}
    )
    providers["fetch_biography"].side_effect = lambda language, title, ua: (
        demo.artist_biography(language, title, ua) | {"text": "Changed online biography"}
    )
    newer = client.post(
        f"/artist/{ALICE}/import/lookup", data={"language": "pt", "refresh_online": "true"}
    )
    assert token(newer) != reviewed
    activity_store._conn.execute(
        "UPDATE artist_query_cache SET fetched_at = '1970-01-01T00:00:00+00:00'"
    )
    for provider in providers.values():
        provider.reset_mock()
        provider.side_effect = AssertionError("Save and local reads must never query a provider")
    saved = client.post(
        f"/artist/{ALICE}/import/apply?artist_q=Alice&artist_page=2",
        data={"review": reviewed, "fields": ["biography"]},
    )
    assert "HX-Redirect" in saved.headers, saved.text
    assert "artist_page=2" in saved.headers["HX-Redirect"]
    profile = artist_nfo.read(path)
    assert profile.biography == demo.artist_biography("pt", "Demo artist", "")["text"]
    assert profile.name == "Alice"
    assert profile.genres == ()  # unselected MusicBrainz tags are preserved locally
    assert profile.biography_source is not None
    assert profile.biography_source["url"] == "https://pt.wikipedia.org/wiki/Demo_artist"
    assert profile.biography_source["revision"] == "12345"
    digest = hashlib.sha256(original).hexdigest()
    assert (cfg.paths.config_dir / "nfoartists" / f"{digest}.nfo").read_bytes() == original
    detail = client.get(saved.headers["HX-Redirect"])
    assert "Creative Commons Attribution-ShareAlike 4.0" in detail.text
    changes = activity_store.nfo_changes_for([e.id for e in activity_store.artist_history(ALICE)])
    change = next(iter(changes.values()))
    assert set(change.changes) == {"biography", "biography_source"}
    assert "pt.wikipedia.org" in change.changes["biography_source"][1]
    before_events = activity_store.artist_history(ALICE)
    again = client.post(
        f"/artist/{ALICE}/import/apply", data={"review": reviewed, "fields": ["biography"]}
    )
    assert "HX-Redirect" in again.headers
    assert activity_store.artist_history(ALICE) == before_events
    undo = client.post(f"/artist/{ALICE}/nfo/restore", data={"event_id": change.event_id})
    assert "HX-Redirect" in undo.headers
    assert path.read_bytes() == original
    assert {p: p.read_bytes() for p in audio} == audio
    for provider in providers.values():
        provider.assert_not_called()


@pytest.mark.parametrize(
    "failure", ["outside-edit", "backup-failure", "foreign-field", "missing-review"]
)
def test_confirmation_refuses_unreviewed_or_unwritable_changes(
    artist_client, providers, monkeypatch, failure
):
    client, cfg, _network = artist_client
    reviewed = token(client.post(f"/artist/{ALICE}/import/lookup", data={"language": "de"}))
    path = source_file(cfg)
    fields = ["biography"]
    if failure == "outside-edit":
        path.write_bytes(path.read_bytes().replace(b"1980", b"1981"))
    elif failure == "backup-failure":
        from harmonist import artist_nfo_edit

        monkeypatch.setattr(artist_nfo_edit, "_keep", Mock(side_effect=OSError("disk full")))
    elif failure == "foreign-field":
        fields = ["mood"]
    else:
        reviewed = "0" * 64
    original = path.read_bytes()
    for provider in providers.values():
        provider.reset_mock()
    result = client.post(
        f"/artist/{ALICE}/import/apply", data={"review": reviewed, "fields": fields}
    )
    assert "HX-Redirect" not in result.headers
    assert BeautifulSoup(result.text, "html.parser").select_one('[role="alert"]')
    assert path.read_bytes() == original
    assert not activity_store.nfo_changes_for([e.id for e in activity_store.artist_history(ALICE)])
    for provider in providers.values():
        provider.assert_not_called()


def test_missing_language_and_failed_biography_never_clear_local_values(artist_client, providers):
    client, cfg, _network = artist_client
    original = source_file(cfg).read_bytes()
    providers["fetch_sitelinks"].side_effect = lambda *args: {"en": "Demo artist"}
    missing = client.post(f"/artist/{ALICE}/import/lookup", data={"language": "it"})
    assert "No linked biography in Italiano" in missing.text
    assert "biography" not in missing.context["import_review"]["values"]
    providers["fetch_sitelinks"].side_effect = artist_lookup.LookupError("Wikidata HTTP 503")
    failed = client.post(
        f"/artist/{ALICE}/import/lookup", data={"language": "pt", "refresh_online": "true"}
    )
    assert "Wikidata HTTP 503" in failed.text
    assert "No linked biography" not in failed.text
    assert source_file(cfg).read_bytes() == original
    saved = client.post(
        f"/artist/{ALICE}/import/apply", data={"review": token(failed), "fields": ["genre"]}
    )
    assert "HX-Redirect" in saved.headers
    profile = artist_nfo.read(source_file(cfg))
    assert profile.biography == "<script>biography</script>"
    assert profile.genres == ("rock", "alternative")


def test_discography_pagination_preserves_selections_and_owned_albums(artist_client, providers):
    client, cfg, _network = artist_client
    reviewed = token(client.post(f"/artist/{ALICE}/import/lookup", data={"language": "en"}))
    providers["fetch_discography"].assert_not_called()
    first = client.post(
        f"/artist/{ALICE}/import/discography", data={"review": reviewed, "fields": ["genre"]}
    )
    page = first.context["import_review"]["discography"]
    assert page["total"] == 22 and len(page["albums"]) == 20
    first_id = page["albums"][0]["id"]
    second = client.post(
        f"/artist/{ALICE}/import/discography",
        data={"review": token(first), "offset": 20, "fields": ["genre"], "albums": [first_id]},
    )
    snapshot = second.context["import_review"]
    second_id = snapshot["discography"]["albums"][0]["id"]
    assert snapshot["selected_fields"] == ["genre"]
    assert first_id in snapshot["picked_albums"]
    assert "21–22 of 22 release groups" in second.text
    for provider in providers.values():
        provider.reset_mock()
    saved = client.post(
        f"/artist/{ALICE}/import/apply",
        data={"review": token(second), "fields": ["genre"], "albums": [first_id, second_id]},
    )
    assert "HX-Redirect" in saved.headers
    profile = artist_nfo.read(source_file(cfg))
    assert {a.release_group_id for a in profile.discography} == {first_id, second_id}
    assert len(client.get(f"/artist/{ALICE}").context["rows"]) == 2
    for provider in providers.values():
        provider.assert_not_called()


def test_unidentified_credit_requires_choice_and_survives_reopen_without_changing_tags(
    artist_client, providers
):
    client, cfg, _network = artist_client
    make_album(cfg.paths.music_dir, "Unidentified copy", RELEASE, ["Alice"], [])
    rows = client.get("/artists").context["artists_view"]["rows"]
    local = next(a for a in rows if a.mbid is None)
    audio = {p: p.read_bytes() for p in cfg.paths.music_dir.rglob("*") if p.is_file()}
    results = client.post(
        f"/artist/{local.key}/import/lookup", data={"language": "it", "query": "Alice"}
    )
    assert "Use this artist" in results.text
    providers["fetch_artist"].assert_not_called()
    chosen = client.post(
        f"/artist/{local.key}/import/lookup", data={"language": "it", "chosen_mbid": ALICE}
    )
    saved = client.post(
        f"/artist/{local.key}/import/apply", data={"review": token(chosen), "fields": ["biography"]}
    )
    assert "HX-Redirect" in saved.headers
    target = cfg.paths.artist_info_dir / local.key / "artist.nfo"
    assert artist_nfo.read(target).mbid == ALICE
    assert artist_nfo.read(target).credit == local.key
    # Re-open the store and discard parser caches as a process restart would.
    activity_store.init(cfg.paths.config_dir / "activity.db")
    artist_nfo._read.cache_clear()
    profile = client.get(saved.headers["HX-Redirect"])
    assert profile.context["nfo"].profile.path == target
    assert profile.context["artist"].mbid == ALICE
    direct = client.get(f"/artist/{local.key}")
    assert direct.context["nfo"].profile is not None
    assert direct.context["nfo"].profile.path == target
    assert client.get(f"/artist/{ALICE}").context["nfo"].profile.path == source_file(cfg)
    assert {p: p.read_bytes() for p in audio} == audio
    change = next(
        iter(
            activity_store.nfo_changes_for(
                [e.id for e in activity_store.artist_history(local.key)]
            ).values()
        )
    )
    undone = client.post(f"/artist/{local.key}/nfo/restore", data={"event_id": change.event_id})
    assert "HX-Redirect" in undone.headers
    assert not target.exists()
    assert client.get(f"/artist/{local.key}").context["artist"].mbid is None


def test_manually_adapted_biography_retains_attribution_and_undo_restores_it(
    artist_client, providers
):
    client, cfg, _network = artist_client
    reviewed = token(client.post(f"/artist/{ALICE}/import/lookup", data={"language": "en"}))
    client.post(f"/artist/{ALICE}/import/apply", data={"review": reviewed, "fields": ["biography"]})
    path = source_file(cfg)
    imported = path.read_bytes()
    current = artist_nfo.read(path)
    result = client.post(
        f"/artist/{ALICE}/nfo",
        data={"expected": current.fingerprint, "biography": "A locally adapted biography"},
    )
    assert "HX-Redirect" in result.headers
    profile = artist_nfo.read(path)
    assert profile.biography_source is not None and current.biography_source is not None
    assert profile.biography_source["modified"] == "true"
    assert profile.biography_source["url"] == current.biography_source["url"]
    assert "edited locally" in client.get(f"/artist/{ALICE}").text
    change = max(
        activity_store.nfo_changes_for(
            [e.id for e in activity_store.artist_history(ALICE)]
        ).values(),
        key=lambda e: e.event_id,
    )
    assert set(change.changes) == {"biography", "biography_source"}
    client.post(f"/artist/{ALICE}/nfo/restore", data={"event_id": change.event_id})
    assert path.read_bytes() == imported


def test_discography_import_preserves_unknown_xml_and_is_repeatable(artist_client, providers):
    client, cfg, _network = artist_client
    group = demo.artist_discography(ALICE, 0)["albums"][0]["id"]
    path = source_file(cfg)
    path.write_text(
        path.read_text().replace(
            "</artist>",
            f'<album custom="keep"><title>Previous title</title><musicbrainzreleasegroupid>{group}</musicbrainzreleasegroupid><rating>5</rating></album></artist>',
        )
    )
    reviewed = token(client.post(f"/artist/{ALICE}/import/lookup", data={"language": "en"}))
    page = client.post(f"/artist/{ALICE}/import/discography", data={"review": reviewed})
    reviewed = token(page)
    applied = client.post(
        f"/artist/{ALICE}/import/apply", data={"review": reviewed, "albums": [group]}
    )
    assert "HX-Redirect" in applied.headers
    assert 'custom="keep"' in path.read_text()
    assert "<rating>5</rating>" in path.read_text()
    assert artist_nfo.read(path).discography[0].title == "Discography album 01"
    events = activity_store.artist_history(ALICE)
    contents = path.read_bytes()
    again = client.post(
        f"/artist/{ALICE}/import/apply", data={"review": reviewed, "albums": [group]}
    )
    assert "HX-Redirect" in again.headers
    assert path.read_bytes() == contents
    assert activity_store.artist_history(ALICE) == events


def test_absent_discography_year_preserves_existing_local_year(artist_client, providers):
    client, cfg, _network = artist_client
    page = demo.artist_discography(ALICE, 0)
    group = page["albums"][0]["id"]
    page["albums"][0]["year"] = ""
    providers["fetch_discography"].side_effect = lambda *args: page
    path = source_file(cfg)
    path.write_text(
        path.read_text().replace(
            "</artist>",
            f"<album><title>Existing title</title><year>2007</year><musicbrainzreleasegroupid>{group}</musicbrainzreleasegroupid></album></artist>",
        )
    )
    reviewed = token(client.post(f"/artist/{ALICE}/import/lookup", data={"language": "en"}))
    discography = client.post(f"/artist/{ALICE}/import/discography", data={"review": reviewed})
    applied = client.post(
        f"/artist/{ALICE}/import/apply", data={"review": token(discography), "albums": [group]}
    )
    assert "HX-Redirect" in applied.headers
    profile = artist_nfo.read(path)
    assert profile.discography[0].title == "Discography album 01"
    assert profile.discography[0].year == "2007"


def test_profile_cache_reuses_by_id_response_and_explicit_refresh_keeps_old_review_on_failure(
    artist_client, providers
):
    client, _cfg, _network = artist_client
    first = client.post(f"/artist/{ALICE}/import/lookup", data={"language": "en"})
    providers["fetch_artist"].assert_called_once_with(ALICE)
    providers["fetch_discography"].assert_not_called()
    second = client.post(f"/artist/{ALICE}/import/lookup", data={"language": "de"})
    providers["fetch_artist"].assert_called_once()
    providers["fetch_sitelinks"].assert_called_once()
    assert providers["fetch_biography"].call_count == 2
    providers["fetch_artist"].side_effect = artist_lookup.LookupError("MusicBrainz HTTP 503")
    failed = client.post(
        f"/artist/{ALICE}/import/lookup",
        data={"language": "de", "refresh_online": "true", "previous_review": token(second)},
    )
    assert "MusicBrainz HTTP 503" in failed.text
    assert token(failed) == token(second)
    assert failed.context["import_review"]["values"] == second.context["import_review"]["values"]
    assert token(first) != token(second)


def test_review_rejects_another_artist_and_records_a_musicbrainz_merge(artist_client, providers):
    client, cfg, _network = artist_client
    providers["fetch_artist"].side_effect = lambda id: demo.artist_metadata(BOB)
    reviewed = token(client.post(f"/artist/{ALICE}/import/lookup", data={"language": "en"}))
    rejected = client.post(
        f"/artist/{BOB}/import/apply", data={"review": reviewed, "fields": ["biography"]}
    )
    assert "HX-Redirect" not in rejected.headers
    assert "does not match" in rejected.text
    applied = client.post(
        f"/artist/{ALICE}/import/apply", data={"review": reviewed, "fields": ["biography"]}
    )
    assert "HX-Redirect" in applied.headers
    profile = artist_nfo.read(source_file(cfg))
    assert profile.mbid == BOB and profile.credit == ALICE
    assert client.get(applied.headers["HX-Redirect"]).context["artist"].mbid == BOB
    change = next(
        iter(
            activity_store.nfo_changes_for(
                [e.id for e in activity_store.artist_history(ALICE)]
            ).values()
        )
    )
    assert change.changes["musicBrainzArtistID"] == [ALICE, BOB]
    undo = client.post(f"/artist/{ALICE}/nfo/restore", data={"event_id": change.event_id})
    assert "HX-Redirect" in undo.headers
    assert artist_nfo.read(source_file(cfg)).mbid == ALICE
