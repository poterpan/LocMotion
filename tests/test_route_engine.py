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

    @pytest.mark.asyncio
    async def test_merges_signals_at_same_intersection(self, engine):
        # 大路口 OSM 每個轉角一個 traffic_signals 節點，沿路線距離相近的要合併成一個停等點
        route = Route(
            polyline=[
                LatLng(lat=25.0, lng=121.5),
                LatLng(lat=25.0, lng=121.52),
            ],
            distance=2016.0, duration=120.0, steps=[],
        )
        # 1 & 2: 沿路線相距 ~20m（同一路口 → 合併）
        # 3 & 4: 再 ~180m 後，也相距 ~20m（另一路口 → 合併）
        overpass_resp = {
            "elements": [
                {"type": "node", "id": 1, "lat": 25.0001, "lon": 121.504959},
                {"type": "node", "id": 2, "lat": 25.0001, "lon": 121.505157},
                {"type": "node", "id": 3, "lat": 25.0001, "lon": 121.506943},
                {"type": "node", "id": 4, "lat": 25.0001, "lon": 121.507141},
            ],
        }
        mock_resp = make_mock_response(overpass_resp)
        with patch.object(engine._client, "get", new_callable=AsyncMock, return_value=mock_resp):
            stops = await engine.find_traffic_signals(route)
        assert len(stops) == 2
        assert 480 < stops[0].distance_along_route < 520
        assert 680 < stops[1].distance_along_route < 720

    @pytest.mark.asyncio
    async def test_chain_merges_consecutive_close_signals(self, engine):
        # 三個 ~30m 間隔形成一串，要合併成單一停等點
        route = Route(
            polyline=[
                LatLng(lat=25.0, lng=121.5),
                LatLng(lat=25.0, lng=121.52),
            ],
            distance=2016.0, duration=120.0, steps=[],
        )
        overpass_resp = {
            "elements": [
                {"type": "node", "id": 1, "lat": 25.0001, "lon": 121.504959},
                {"type": "node", "id": 2, "lat": 25.0001, "lon": 121.505257},
                {"type": "node", "id": 3, "lat": 25.0001, "lon": 121.505555},
            ],
        }
        mock_resp = make_mock_response(overpass_resp)
        with patch.object(engine._client, "get", new_callable=AsyncMock, return_value=mock_resp):
            stops = await engine.find_traffic_signals(route)
        assert len(stops) == 1


class TestTrafficSignalsCache:
    def _route(self, lat0, lng0):
        return Route(
            polyline=[
                LatLng(lat=lat0, lng=lng0),
                LatLng(lat=lat0 + 0.01, lng=lng0 + 0.01),
                LatLng(lat=lat0 + 0.02, lng=lng0 + 0.02),
            ],
            distance=5000.0, duration=300.0, steps=[],
        )

    @pytest.mark.asyncio
    async def test_same_region_second_call_hits_cache(self, engine):
        route = self._route(25.0, 121.5)
        mock_get = AsyncMock(return_value=make_mock_response(OVERPASS_RESPONSE))
        with patch.object(engine._client, "get", mock_get):
            stops1 = await engine.find_traffic_signals(route)
            stops2 = await engine.find_traffic_signals(route)
        # 第二次同區域走快取，不再打 Overpass
        assert mock_get.call_count == 1
        # 快取回傳的結果與首次一致
        assert len(stops1) == len(stops2) == 2

    @pytest.mark.asyncio
    async def test_different_region_refetches(self, engine):
        route_a = self._route(25.0, 121.5)
        route_b = self._route(24.0, 120.5)  # 一個 degree 外，不同網格
        mock_get = AsyncMock(return_value=make_mock_response(OVERPASS_RESPONSE))
        with patch.object(engine._client, "get", mock_get):
            await engine.find_traffic_signals(route_a)
            await engine.find_traffic_signals(route_b)
        assert mock_get.call_count == 2

    @pytest.mark.asyncio
    async def test_empty_result_not_cached(self, engine):
        # Overpass 回 200 但空 elements（不穩 mirror 的典型壞回應）不應被快取
        route = self._route(25.0, 121.5)
        mock_get = AsyncMock(return_value=make_mock_response({"elements": []}))
        with patch.object(engine._client, "get", mock_get), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            await engine.find_traffic_signals(route)
            n1 = mock_get.call_count
            await engine.find_traffic_signals(route)
            n2 = mock_get.call_count
        assert n1 > 0
        assert n2 == 2 * n1  # 第二次沒走快取，重新查了一遍

    @pytest.mark.asyncio
    async def test_lru_evicts_oldest(self, engine):
        engine._signal_cache_max = 2
        mock_get = AsyncMock(return_value=make_mock_response(OVERPASS_RESPONSE))
        with patch.object(engine._client, "get", mock_get):
            await engine.find_traffic_signals(self._route(25.0, 121.5))  # A miss -> 1
            await engine.find_traffic_signals(self._route(24.0, 120.5))  # B miss -> 2
            await engine.find_traffic_signals(self._route(23.0, 119.5))  # C miss -> 3, evict A
            await engine.find_traffic_signals(self._route(24.0, 120.5))  # B still cached -> hit
            await engine.find_traffic_signals(self._route(25.0, 121.5))  # A evicted -> miss -> 4
        assert mock_get.call_count == 4


class TestOverpassQuery:
    @pytest.mark.asyncio
    async def test_prefers_nonempty_over_empty_200(self, engine):
        # 壞 mirror 回 200 但空（如 osm.ch），不能因此就採用；要繼續找有資料的回應
        responses = [
            make_mock_response({"elements": []}),  # ep1: 200 但空
            make_mock_response({"elements": [
                {"type": "node", "id": 1, "lat": 25.0, "lon": 121.5},
            ]}),                                   # ep2: 200 有資料
        ]
        mock_get = AsyncMock(side_effect=responses)
        with patch.object(engine._client, "get", mock_get), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            elements = await engine._overpass_query("q")
        assert elements is not None
        assert len(elements) == 1

    @pytest.mark.asyncio
    async def test_returns_empty_when_all_200_empty(self, engine):
        # 全部 endpoint 都 200 但空 → 視為該區真的沒紅綠燈，回 []（非 None）
        mock_get = AsyncMock(return_value=make_mock_response({"elements": []}))
        with patch.object(engine._client, "get", mock_get), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            elements = await engine._overpass_query("q")
        assert elements == []

    @pytest.mark.asyncio
    async def test_returns_none_when_all_fail(self, engine):
        # 全部 endpoint 都非 200 → None（讓上層跳過紅綠燈、下次重試）
        mock_get = AsyncMock(return_value=make_mock_response({}, status=504))
        with patch.object(engine._client, "get", mock_get), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            elements = await engine._overpass_query("q")
        assert elements is None


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
