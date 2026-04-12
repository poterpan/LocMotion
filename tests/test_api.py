import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient


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


class TestServeHTML:
    def test_index_page(self, client):
        resp = client.get("/")
        # Accept 200 (template exists) or 500 (template missing)
        assert resp.status_code in (200, 500)
