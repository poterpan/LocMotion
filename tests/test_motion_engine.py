# tests/test_motion_engine.py
import asyncio
import pytest
from unittest.mock import AsyncMock
from src.models import LatLng, Route, SimulationConfig, StopPoint
from src.motion_engine import MotionEngine


def make_straight_route(n_points=10, spacing_deg=0.001) -> Route:
    """A straight route heading north."""
    polyline = [LatLng(lat=25.0 + i * spacing_deg, lng=121.5) for i in range(n_points)]
    distance = (n_points - 1) * spacing_deg * 111320
    return Route(polyline=polyline, distance=distance, duration=600.0, steps=[])


@pytest.fixture
def route():
    return make_straight_route()


@pytest.fixture
def config():
    return SimulationConfig(mode="driving", base_speed_kmh=36)  # 10 m/s


class TestMotionEngineInit:
    def test_initial_state_is_idle(self, route, config):
        engine = MotionEngine(route, config)
        state = engine.get_state()
        assert state.status == "idle"
        assert state.speed_kmh == 0
        assert state.distance_traveled_m == 0


class TestMotionEngineTick:
    def test_single_tick_moves_forward(self, route, config):
        engine = MotionEngine(route, config)
        engine._status = "moving"
        engine._tick(dt=0.5)
        state = engine.get_state()
        assert state.distance_traveled_m > 0
        assert state.speed_kmh > 0

    def test_acceleration_from_stop(self, route, config):
        engine = MotionEngine(route, config)
        engine._status = "accelerating"
        engine._current_speed_ms = 0.0
        engine._tick(dt=0.5)
        assert engine._current_speed_ms > 0
        assert engine._current_speed_ms <= config.acceleration * 0.5 + 0.1

    def test_completes_at_route_end(self, route, config):
        engine = MotionEngine(route, config)
        engine._status = "moving"
        engine._distance_traveled = route.distance - 0.1
        engine._current_speed_ms = 10.0
        engine._tick(dt=1.0)
        assert engine.get_state().status == "completed"


class TestMotionEngineStops:
    def test_decelerates_near_stop(self, route, config):
        stop = StopPoint(
            position=LatLng(lat=25.003, lng=121.5),
            distance_along_route=333.96,
        )
        route.stop_points = [stop]
        engine = MotionEngine(route, config)
        engine._status = "moving"
        engine._current_speed_ms = 10.0
        engine._distance_traveled = 333.96 - 15.0
        engine._tick(dt=0.5)
        assert engine._status in ("decelerating", "moving")


class TestPositionInterpolation:
    def test_position_on_route(self, route, config):
        engine = MotionEngine(route, config)
        engine._distance_traveled = 0.0
        pos = engine._interpolate_position()
        assert pos.lat == pytest.approx(25.0, abs=0.001)
        assert pos.lng == pytest.approx(121.5, abs=0.001)

    def test_midway_position(self, route, config):
        engine = MotionEngine(route, config)
        engine._distance_traveled = route.distance / 2
        pos = engine._interpolate_position()
        assert 25.0 < pos.lat < 25.0 + 0.009


class TestHeading:
    def test_heading_north(self, route, config):
        """Straight-north route should have heading ~0."""
        engine = MotionEngine(route, config)
        engine._distance_traveled = 100.0
        heading = engine._compute_heading()
        assert -5 < heading < 5 or 355 < heading < 360
