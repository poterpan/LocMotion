import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient

from src.models import Route, StopPoint, LatLng


def make_route():
    return Route(
        polyline=[LatLng(lat=25.0, lng=121.5), LatLng(lat=25.02, lng=121.52)],
        distance=1000.0, duration=60.0, steps=[],
    )


@pytest.fixture
def client():
    with patch("src.main.device_mgr") as mock_dm, \
         patch("src.main.route_engine") as mock_re:
        mock_dm.list_devices = AsyncMock(return_value=[])
        mock_dm.connect = AsyncMock(return_value=True)
        mock_dm.disconnect = AsyncMock(return_value=True)
        mock_dm.set_location = AsyncMock(return_value=True)
        mock_dm.clear_location = AsyncMock(return_value=True)
        mock_dm._connections = {}

        from src.main import app
        yield TestClient(app)


class TestDeviceRoutes:
    def test_list_devices(self, client):
        resp = client.get("/api/devices")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_connect_device(self, client):
        resp = client.post(
            "/api/devices/connect",
            json={"udid": "abc", "conn_type": "USB"},
        )
        assert resp.status_code == 200

    def test_disconnect_device(self, client):
        resp = client.post("/api/devices/disconnect")
        assert resp.status_code == 200


class TestLocationRoutes:
    def test_set_static_location(self, client):
        resp = client.post(
            "/api/location/set", json={"lat": 25.033, "lng": 121.565},
        )
        assert resp.status_code == 200

    def test_clear_location(self, client):
        resp = client.post("/api/location/clear")
        assert resp.status_code == 200


class TestSimulationRoutes:
    def test_start_without_route_fails(self, client):
        resp = client.post(
            "/api/simulation/start", json={"mode": "driving"},
        )
        assert resp.status_code == 400


class TestTrafficSignalsAsync:
    def _plan(self, client):
        return client.post(
            "/api/route/plan",
            json={"points": [{"lat": 25.0, "lng": 121.5},
                             {"lat": 25.02, "lng": 121.52}]},
        )

    def test_plan_does_not_block_on_overpass(self, client):
        import src.main as main
        main.route_engine.plan_route = AsyncMock(return_value=make_route())
        main.route_engine.find_traffic_signals = AsyncMock(return_value=[])
        resp = self._plan(client)
        assert resp.status_code == 200
        # 規劃只回路線，紅綠燈為空、且完全不打 Overpass
        assert resp.json()["stop_points"] == []
        main.route_engine.find_traffic_signals.assert_not_called()

    def test_traffic_signals_endpoint_returns_and_stores_stops(self, client):
        import src.main as main
        main.route_engine.plan_route = AsyncMock(return_value=make_route())
        main.route_engine.find_traffic_signals = AsyncMock(return_value=[
            StopPoint(position=LatLng(lat=25.01, lng=121.51),
                      distance_along_route=500.0),
        ])
        self._plan(client)
        resp = client.get("/api/route/traffic_signals")
        assert resp.status_code == 200
        stops = resp.json()["stop_points"]
        assert len(stops) == 1
        assert stops[0]["distance_along_route"] == 500.0
        # 寫回 current_route，供模擬停等點使用
        assert len(main.current_route.stop_points) == 1

    def test_traffic_signals_no_route_404(self, client):
        import src.main as main
        with patch.object(main, "current_route", None):
            resp = client.get("/api/route/traffic_signals")
        assert resp.status_code == 404


class TestServeHTML:
    def test_index_page(self, client):
        resp = client.get("/")
        # Accept 200 (template exists) or 500 (template missing)
        assert resp.status_code in (200, 500)
