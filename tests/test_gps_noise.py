# tests/test_gps_noise.py
import math
import pytest
from src.gps_noise import GPSNoiseEngine


class TestAddJitter:
    def test_jitter_returns_different_coords(self):
        """Jitter should modify the coordinates (extremely unlikely to be identical)."""
        engine = GPSNoiseEngine(seed=42)
        lat, lng = engine.add_jitter(25.033, 121.565, radius_m=3.0)
        assert (lat, lng) != (25.033, 121.565)

    def test_jitter_stays_within_radius(self):
        """Over many samples, all points should be within ~3x the radius (3-sigma)."""
        engine = GPSNoiseEngine(seed=42)
        base_lat, base_lng = 25.033, 121.565
        max_offset_m = 0
        for _ in range(1000):
            lat, lng = engine.add_jitter(base_lat, base_lng, radius_m=3.0)
            dlat_m = (lat - base_lat) * 111320
            dlng_m = (lng - base_lng) * 111320 * math.cos(math.radians(base_lat))
            dist = math.sqrt(dlat_m**2 + dlng_m**2)
            max_offset_m = max(max_offset_m, dist)
        # 3-sigma boundary: 99.7% within radius. Allow some slack.
        assert max_offset_m < 15.0  # 5x radius as hard upper bound

    def test_zero_radius_returns_original(self):
        engine = GPSNoiseEngine(seed=42)
        lat, lng = engine.add_jitter(25.033, 121.565, radius_m=0.0)
        assert lat == 25.033
        assert lng == 121.565


class TestStationaryDrift:
    def test_drift_returns_nearby_point(self):
        engine = GPSNoiseEngine(seed=42)
        lat, lng = engine.stationary_drift(25.033, 121.565, radius_m=5.0, elapsed_sec=10.0)
        dlat_m = (lat - 25.033) * 111320
        dlng_m = (lng - 121.565) * 111320 * math.cos(math.radians(25.033))
        dist = math.sqrt(dlat_m**2 + dlng_m**2)
        assert dist < 20.0  # well within bounds

    def test_drift_changes_over_time(self):
        engine = GPSNoiseEngine(seed=42)
        p1 = engine.stationary_drift(25.033, 121.565, radius_m=5.0, elapsed_sec=0.0)
        p2 = engine.stationary_drift(25.033, 121.565, radius_m=5.0, elapsed_sec=30.0)
        assert p1 != p2  # different elapsed -> different position


class TestSeedReproducibility:
    def test_same_seed_same_output(self):
        e1 = GPSNoiseEngine(seed=99)
        e2 = GPSNoiseEngine(seed=99)
        assert e1.add_jitter(25.0, 121.0, 3.0) == e2.add_jitter(25.0, 121.0, 3.0)
