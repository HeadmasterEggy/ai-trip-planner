"""The live tool adapters: what URL they ask for, and how often.

The fixtures are the default path, so nothing else here exercises the
OpenStreetMap adapter. These tests drive it directly with the transport
replaced by a recorder, so they never touch the network.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from trip_planner.tools import maps


@pytest.fixture
def live(monkeypatch):
    """Live mode, returning the URLs the adapter asked for."""
    monkeypatch.setenv("USE_MOCK_TOOLS", "false")
    seen: list[str] = []

    def fake_get(url: str) -> object:
        seen.append(url)
        if "/search?" in url:
            return [{"lat": "35.0", "lon": "135.0", "name": "A place"}]
        return {"routes": [{"duration": 600}]}

    monkeypatch.setattr(maps, "_osm_get", fake_get)
    return seen


def _query(url: str) -> dict[str, list[str]]:
    return parse_qs(urlparse(url).query)


def test_a_multi_city_query_is_sent_as_one_escaped_value(live):
    """One query value, not a value plus a stray parameter.

    "&" used to travel unescaped, so Nominatim saw `q=sight in Tokyo ` and
    answered for the first city only -- with no error anywhere.
    """
    maps.MapsAdapter().places(near="Tokyo & Kyoto", category="sight")
    url = live[0]
    assert _query(url)["q"] == ["sight in Tokyo & Kyoto"]
    assert "%26" in url
    assert "Kyoto" in _query(url)["q"][0]


def test_an_optional_category_does_not_leak_into_the_query(live):
    maps.MapsAdapter().places(near="Kyoto")
    assert _query(live[0])["q"] == ["sight in Kyoto"]


def test_a_repeated_location_is_geocoded_once(live):
    """The itinerary re-checks the same pairs every round, and Nominatim's
    usage policy is one request per second."""
    adapter = maps.MapsAdapter()
    first = adapter.route(frm="Kyoto", to="Tokyo")
    after_first = list(live)
    second = adapter.route(frm="Kyoto", to="Tokyo")

    assert first == second
    # Two geocodes plus one route on the first call. The second call reuses both
    # geocodes and only asks OSRM again.
    assert len([url for url in after_first if "/search?" in url]) == 2
    assert len([url for url in live if "/search?" in url]) == 2
    assert len(live) == 4
