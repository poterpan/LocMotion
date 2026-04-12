import pytest
from src.models import (
    LatLng, DeviceInfo, RouteStep, Route, StopPoint,
    SimulationConfig, SimulationState,
)


def test_latlng_creation():
    p = LatLng(lat=25.033, lng=121.565)
    assert p.lat == 25.033
    assert p.lng == 121.565


def test_device_info():
    d = DeviceInfo(
        udid="abc123", name="iPhone", ios_version="17.4",
        conn_type="USB",
    )
    assert d.udid == "abc123"


def test_simulation_config_driving_defaults():
    c = SimulationConfig(mode="driving")
    assert c.base_speed_kmh == 50
    assert c.acceleration == 2.5
    assert c.deceleration == 3.0
    assert c.jitter_meters == 3.0
    assert c.stop_probability == 0.5
    assert c.stop_duration_range == (15, 45)


def test_simulation_config_walking_defaults():
    c = SimulationConfig(mode="walking")
    assert c.base_speed_kmh == 5
    assert c.acceleration == 1.0
    assert c.stop_probability == 0.3


def test_simulation_config_cycling_defaults():
    c = SimulationConfig(mode="cycling")
    assert c.base_speed_kmh == 18
    assert c.acceleration == 1.5


def test_simulation_config_custom_override():
    c = SimulationConfig(mode="driving", base_speed_kmh=80)
    assert c.base_speed_kmh == 80
    assert c.acceleration == 2.5  # other defaults unchanged


def test_route_distance_and_stops():
    r = Route(
        polyline=[LatLng(lat=0, lng=0), LatLng(lat=1, lng=1)],
        distance=1000.0,
        duration=60.0,
        steps=[],
    )
    assert r.distance == 1000.0
    assert len(r.polyline) == 2


def test_stop_point():
    sp = StopPoint(
        position=LatLng(lat=25.0, lng=121.5),
        distance_along_route=500.0,
    )
    assert sp.distance_along_route == 500.0


def test_simulation_state_values():
    s = SimulationState(
        lat=25.0, lng=121.5, speed_kmh=42.3, heading_deg=187.5,
        distance_traveled_m=1523, distance_remaining_m=3477,
        eta_seconds=298, status="moving",
        next_stop_distance_m=312, elapsed_seconds=187,
    )
    assert s.status == "moving"
    assert s.speed_kmh == 42.3
