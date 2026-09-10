"""Maps / Places adapter.

The deterministic fixture is the default so the app runs with no keys and no
network. Setting USE_MOCK_TOOLS=false switches to OpenStreetMap (Nominatim +
OSRM), which is free but rate-limited -- set a descriptive OSM_USER_AGENT in
any deployment.
"""

from __future__ import annotations

import os

import httpx

from ..ports import Place, RouteLeg

_TIMEOUT = httpx.Timeout(8.0)
DEFAULT_NOMINATIM_BASE = "https://nominatim.openstreetmap.org"
DEFAULT_OSRM_BASE = "https://router.project-osrm.org"


def _mock_enabled() -> bool:
    return os.getenv("USE_MOCK_TOOLS", "true").lower() != "false"


def _osm_get(url: str) -> object:
    headers = {
        "accept": "application/json",
        "user-agent": os.getenv("OSM_USER_AGENT", "ai-trip-planner/1.0 (local development)"),
    }
    response = httpx.get(url, headers=headers, timeout=_TIMEOUT, follow_redirects=True)
    response.raise_for_status()
    return response.json()


def _nominatim_base() -> str:
    return os.getenv("NOMINATIM_BASE_URL", DEFAULT_NOMINATIM_BASE)


def _search_url(base: str, *, limit: str, query: str) -> str:
    """A Nominatim search URL with the query escaped as a single value.

    Interpolating `httpx.URL(query)` was not enough: it leaves `&` alone, so the
    demo's "Tokyo & Kyoto" went out as `q=sight in Tokyo ` plus a stray empty
    parameter, and every live lookup silently answered for the first city only.
    """
    params = httpx.QueryParams({"format": "jsonv2", "limit": limit, "q": query})
    return f"{base}/search?{params}"


def _geocode_at(base: str, query: str) -> tuple[float, float] | None:
    results = _osm_get(_search_url(base, limit="1", query=query))
    if not isinstance(results, list) or not results:
        return None
    return float(results[0]["lat"]), float(results[0]["lon"])


class MapsAdapter:
    def __init__(self) -> None:
        # One lookup per (host, query) per adapter. The itinerary re-checks the
        # same location pairs on every negotiation round, and Nominatim's usage
        # policy is one request per second.
        self._geocoded: dict[str, tuple[float, float] | None] = {}

    def _geocode(self, query: str) -> tuple[float, float] | None:
        base = _nominatim_base()
        key = f"{base}|{query}"
        if key not in self._geocoded:
            self._geocoded[key] = _geocode_at(base, query)
        return self._geocoded[key]

    def route(self, *, frm: str, to: str, date: str | None = None) -> list[RouteLeg]:
        if _mock_enabled():
            return [RouteLeg("train", 140, 90.0, f"mock {frm} -> {to}")]
        try:
            origin, destination = self._geocode(frm), self._geocode(to)
            if not origin or not destination:
                return []
            base = os.getenv("OSRM_BASE_URL", DEFAULT_OSRM_BASE)
            data = _osm_get(
                f"{base}/route/v1/driving/{origin[1]},{origin[0]};"
                f"{destination[1]},{destination[0]}?overview=false"
            )
            routes = data.get("routes") if isinstance(data, dict) else None
            if not routes:
                return []
            minutes = max(1, round(routes[0].get("duration", 0) / 60))
            return [RouteLeg("transit", minutes, 0.0, f"OSRM driving estimate {frm} -> {to}")]
        except (httpx.HTTPError, KeyError, ValueError):
            # A live provider being unreachable must not fail the plan; the
            # specialist treats an empty result as "no grounded route".
            return []

    def places(self, *, near: str, category: str | None = None) -> list[Place]:
        kind = category or "sight"
        if _mock_enabled():
            return [Place(f"Mock {kind} near {near}", kind, 4.5)]
        try:
            results = _osm_get(_search_url(_nominatim_base(), limit="5", query=f"{kind} in {near}"))
            if not isinstance(results, list):
                return []
            return [
                Place(str(r.get("name") or r.get("display_name", "")).split(",")[0], kind, None)
                for r in results
                if r.get("name") or r.get("display_name")
            ]
        except (httpx.HTTPError, ValueError):
            return []


def create_tool_gateway():
    from ..ports import ToolGateway

    return ToolGateway(
        maps=MapsAdapter(),
        booking=__import__("trip_planner.tools.booking", fromlist=["MockBooking"]).MockBooking(),
    )
