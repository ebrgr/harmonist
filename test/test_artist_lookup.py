"""Provider contracts: shared MusicBrainz client and bounded Wikimedia APIs."""

from unittest.mock import Mock

import httpx
import pytest

from harmonist import artist_lookup, mb_lookup
from test.test_artist_web import ALICE, BOB


def wikipedia_response():
    return {
        "query": {
            "pages": [
                {
                    "title": "Ryan Adams",
                    "fullurl": "https://pt.wikipedia.org/wiki/Ryan_Adams",
                    "extract": "Texto biográfico.",
                    "revisions": [{"revid": 42}],
                }
            ],
            "rightsinfo": {
                "text": "Creative Commons Attribution-ShareAlike 4.0",
                "url": "https://creativecommons.org/licenses/by-sa/4.0/",
            },
        }
    }


def test_artist_by_id_uses_shared_client_and_returns_surviving_identity(monkeypatch):
    fetch = Mock(return_value={"artist": {"id": BOB, "name": "Surviving identity"}})
    monkeypatch.setattr(artist_lookup.musicbrainzngs, "get_artist_by_id", fetch)
    release_includes = mb_lookup.RELEASE_INCLUDES
    result = artist_lookup.fetch_artist(ALICE)
    assert result["id"] == BOB
    fetch.assert_called_once_with(ALICE, includes=["aliases", "tags", "url-rels"])
    assert mb_lookup.RELEASE_INCLUDES == release_includes


def test_active_xml_relationship_strings_do_not_hide_wikidata_or_wikipedia():
    data = {
        "url-relation-list": [
            {
                "type": "wikidata",
                "target": "https://www.wikidata.org/wiki/Q437970",
                "ended": "false",
            },
            {"type": "wikidata", "target": "https://www.wikidata.org/wiki/Q1", "ended": "true"},
            {
                "type": "wikipedia",
                "target": "https://pt.wikipedia.org/wiki/Ryan_Adams",
                "ended": "false",
            },
            {"type": "wikipedia", "target": "https://evil.example/wiki/Ryan_Adams"},
        ]
    }
    assert artist_lookup.wikidata_id(data) == "Q437970"
    assert artist_lookup.wikipedia_links(data) == {"pt": "Ryan_Adams"}
    data["url-relation-list"].append(
        {"type": "wikidata", "target": "https://www.wikidata.org/wiki/Q2"}
    )
    with pytest.raises(artist_lookup.LookupError, match="multiple Wikidata"):
        artist_lookup.wikidata_id(data)


def test_biography_uses_one_official_api_request_with_attribution_and_user_agent(monkeypatch):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=wikipedia_response())

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(artist_lookup.httpx, "stream", client.stream)
        biography = artist_lookup.fetch_biography(
            "pt", "Ryan Adams", "Harmonist/test ( contact@example.org )"
        )
    assert len(requests) == 1
    request = requests[0]
    assert request.url.host == "pt.wikipedia.org"
    assert request.url.path == "/w/api.php"
    assert request.url.params["prop"] == "extracts|info|revisions|pageprops"
    assert request.url.params["exintro"] == request.url.params["explaintext"] == "1"
    assert request.url.params["siprop"] == "rightsinfo"
    assert request.headers["user-agent"] == "Harmonist/test ( contact@example.org )"
    assert biography["text"] == "Texto biográfico."
    assert biography["revision"] == "42"
    assert biography["license_url"] == "https://creativecommons.org/licenses/by-sa/4.0/"


@pytest.mark.parametrize(
    "kind", ["http-503", "api-error", "bad-query", "missing-page", "no-attribution", "oversized"]
)
def test_wikimedia_errors_are_not_empty_biographies(monkeypatch, kind):
    response = wikipedia_response()
    status = 200
    if kind == "http-503":
        status = 503
    elif kind == "api-error":
        response = {"error": {"code": "maxlag"}}
    elif kind == "bad-query":
        response = {"query": []}
    elif kind == "missing-page":
        response["query"]["pages"] = [{"missing": True}]
    elif kind == "no-attribution":
        response["query"]["rightsinfo"] = {}
    else:
        response["query"]["pages"][0]["extract"] = "x" * (2 * 1024 * 1024)
    with httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(status, json=response))
    ) as client:
        monkeypatch.setattr(artist_lookup.httpx, "stream", client.stream)
        with pytest.raises(artist_lookup.LookupError):
            artist_lookup.fetch_biography(
                "pt", "Ryan Adams", "Harmonist/test ( contact@example.org )"
            )


def test_sitelinks_are_api_results_and_missing_entity_is_a_failure(monkeypatch):
    api = Mock(
        return_value={
            "entities": {
                "Q437970": {
                    "sitelinks": {
                        "ptwiki": {"title": "Ryan Adams"},
                        "enwiki": {"title": "Ryan Adams"},
                        "commonswiki": {"title": "Category:Ryan Adams"},
                    }
                }
            }
        }
    )
    monkeypatch.setattr(artist_lookup, "_api", api)
    assert artist_lookup.fetch_sitelinks("Q437970", "Agent") == {
        "en": "Ryan Adams",
        "pt": "Ryan Adams",
    }
    assert api.call_args.args[0] == "www.wikidata.org"
    assert api.call_args.args[1]["props"] == "sitelinks"
    api.return_value = {"entities": {"Q437970": {"missing": True}}}
    with pytest.raises(artist_lookup.LookupError, match="unavailable"):
        artist_lookup.fetch_sitelinks("Q437970", "Agent")


def test_discography_is_one_bounded_page_and_rejects_incomplete_counts(monkeypatch):
    browse = Mock(
        return_value={
            "release-group-list": [
                {"id": BOB, "title": "Album", "first-release-date": "2004-02-01"}
            ],
            "release-group-count": "21",
        }
    )
    monkeypatch.setattr(artist_lookup.musicbrainzngs, "browse_release_groups", browse)
    result = artist_lookup.fetch_discography(ALICE, 20)
    assert result == {
        "albums": [{"id": BOB, "title": "Album", "year": "2004"}],
        "total": 21,
        "offset": 20,
    }
    browse.assert_called_once_with(artist=ALICE, offset=20, limit=20)
    browse.return_value = {"release-group-list": []}
    with pytest.raises(artist_lookup.LookupError, match="incomplete"):
        artist_lookup.fetch_discography(ALICE, 0)
