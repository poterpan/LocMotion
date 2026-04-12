import json
import math
import pytest
import httpx
from unittest.mock import AsyncMock, patch, MagicMock
from src.models import LatLng, Route
from src.route_engine import RouteEngine

# --- Fixtures ---

OSRM_RESPONSE = {
    "code": "Ok",
    "routes": [{
        "distance": 5000.0,
        "duration": 300.0,
        "geometry": {
            "coordinates": [[121.5, 25.0], [121.51, 25.01], [121.52, 25.02]],
            "type": "LineString",
        },
        "legs": [{
            "steps": [{
                "maneuver": {"type": "depart", "location": [121.5, 25.0]},
                "distance": 2500.0,
                "duration": 150.0,
            }, {
                "maneuver": {"type": "arrive", "location": [121.52, 25.02]},
                "distance": 2500.0,
                "duration": 150.0,
            }],
        }],
    }],
}

OVERPASS_RESPONSE = {
    "elements": [
        {"type": "node", "id": 1, "lat": 25.005, "lon": 121.505},
        {"type": "node", "id": 2, "lat": 25.015, "lon": 121.515},
        {"type": "node", "id": 3, "lat": 99.0, "lon": 99.0},  # far away, should be filtered
    ],
}


def make_mock_response(data, status=200):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    return resp


@pytest.fixture
def engine():
    return RouteEngine()


# --- Tests ---

class TestPlanRoute:
    @pytest.mark.asyncio
    async def test_plan_route_parses_osrm(self, engine):
        mock_resp = make_mock_response(OSRM_RESPONSE)
        with patch.object(engine._client, "get", new_callable=AsyncMock, return_value=mock_resp):
            route = await engine.plan_route(
                start=LatLng(lat=25.0, lng=121.5),
                end=LatLng(lat=25.02, lng=121.52),
                mode="driving",
            )
        assert isinstance(route, Route)
        assert route.distance == 5000.0
        assert route.duration == 300.0
        assert len(route.polyline) == 3
        assert route.polyline[0].lat == 25.0
        assert route.polyline[0].lng == 121.5
        assert len(route.steps) == 2


class TestFindTrafficSignals:
    @pytest.mark.asyncio
    async def test_filters_far_signals(self, engine):
        route = Route(
            polyline=[
                LatLng(lat=25.0, lng=121.5),
                LatLng(lat=25.01, lng=121.51),
                LatLng(lat=25.02, lng=121.52),
            ],
            distance=5000.0, duration=300.0, steps=[],
        )
        mock_resp = make_mock_response(OVERPASS_RESPONSE)
        with patch.object(engine._client, "get", new_callable=AsyncMock, return_value=mock_resp):
            stops = await engine.find_traffic_signals(route)
        # Node 3 at (99, 99) should be filtered out
        assert len(stops) == 2
        # Should be sorted by distance_along_route
        assert stops[0].distance_along_route <= stops[1].distance_along_route


class TestSnapToRoute:
    def test_point_on_segment(self, engine):
        """A point directly on the route has distance 0."""
        A = LatLng(lat=0.0, lng=0.0)
        B = LatLng(lat=0.0, lng=1.0)
        P = LatLng(lat=0.0, lng=0.5)
        dist, frac = engine._point_to_segment_distance(P, A, B)
        assert dist < 1.0  # essentially 0 in meters

    def test_point_perpendicular(self, engine):
        A = LatLng(lat=0.0, lng=0.0)
        B = LatLng(lat=0.0, lng=1.0)
        P = LatLng(lat=0.001, lng=0.5)  # slightly off route
        dist, frac = engine._point_to_segment_distance(P, A, B)
        assert dist > 0
        assert 0.0 <= frac <= 1.0


class TestParseGPX:
    def test_parse_gpx_basic(self, engine):
        gpx_content = b"""<?xml version="1.0"?>
        <gpx version="1.1">
          <trk><trkseg>
            <trkpt lat="25.0" lon="121.5"/>
            <trkpt lat="25.01" lon="121.51"/>
            <trkpt lat="25.02" lon="121.52"/>
          </trkseg></trk>
        </gpx>"""
        route = engine.parse_gpx(gpx_content)
        assert len(route.polyline) == 3
        assert route.polyline[0].lat == 25.0
        assert route.distance > 0


class TestGeocode:
    @pytest.mark.asyncio
    async def test_geocode_returns_results(self, engine):
        nominatim_resp = [
            {"lat": "25.0339", "lon": "121.5645", "display_name": "Taipei 101"},
        ]
        mock_resp = make_mock_response(nominatim_resp)
        with patch.object(engine._client, "get", new_callable=AsyncMock, return_value=mock_resp):
            results = await engine.geocode("Taipei 101")
        assert len(results) == 1
        assert results[0].lat == pytest.approx(25.0339)
