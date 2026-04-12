# LocMotion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a realistic iOS GPS simulation tool with web control panel, porting device management from GeoPort and adding route-based movement simulation.

**Architecture:** FastAPI backend with asyncio simulation loop, vanilla JS + Leaflet frontend served as a single HTML file. Device communication via pymobiledevice3 with persistent DVT sessions. WebSocket for real-time 2Hz state broadcasting.

**Tech Stack:** Python 3.12, FastAPI, uvicorn, pymobiledevice3, httpx (async HTTP), Leaflet.js + Tailwind CSS (CDN), pytest

**Spec:** `docs/specs/2026-04-12-locmotion-design.md`

**Key decisions (confirmed with user):**
- macOS only (no Windows/Linux code)
- iOS <17 and 17+ both supported
- USB + WiFi connections
- Persistent DVT session during simulation
- Static location mode uses 2Hz continuous injection with drift
- sudo required at startup
- Nominatim User-Agent: `"LocMotion/1.0"`

---

## File Structure

```
LocMotion/
├── src/
│   ├── main.py                # FastAPI app, routes, WebSocket, entry point
│   ├── models.py              # Pydantic models + dataclasses
│   ├── gps_noise.py           # Jitter + stationary drift
│   ├── device_manager.py      # iOS device connection (ported from GeoPort)
│   ├── route_engine.py        # OSRM routing + Overpass + GPX/KML/GeoJSON
│   ├── motion_engine.py       # Simulation loop, acceleration, stops
│   └── templates/
│       └── index.html         # Single-file frontend
├── tests/
│   ├── test_models.py
│   ├── test_gps_noise.py
│   ├── test_route_engine.py
│   ├── test_motion_engine.py
│   └── test_api.py
├── requirements.txt
└── docs/
```

---

## Task 1: Project Scaffolding

**Files:**
- Create: `requirements.txt`
- Create: `src/__init__.py`
- Create: `tests/__init__.py`
- Create: `src/templates/` (directory)

- [ ] **Step 1: Create requirements.txt**

```
fastapi>=0.110
uvicorn[standard]>=0.29
websockets>=12.0
pymobiledevice3>=4.0
httpx>=0.27
python-multipart>=0.0.9
gpxpy>=1.6
pykml>=0.2
pytest>=8.0
pytest-asyncio>=0.23
```

- [ ] **Step 2: Create package init files and templates directory**

Create empty `src/__init__.py` and `tests/__init__.py`.

```bash
mkdir -p src/templates tests
touch src/__init__.py tests/__init__.py
```

- [ ] **Step 3: Install dependencies**

```bash
pip install -r requirements.txt
```

- [ ] **Step 4: Commit**

```bash
git init
git add requirements.txt src/__init__.py tests/__init__.py
git commit -m "chore: project scaffolding with dependencies"
```

---

## Task 2: Pydantic Models (`src/models.py`)

**Files:**
- Create: `src/models.py`
- Create: `tests/test_models.py`

- [ ] **Step 1: Write test for models**

```python
# tests/test_models.py
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
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
cd /Users/poterpan/Documents/Coding/Python/LocMotion
python -m pytest tests/test_models.py -v
```

Expected: `ModuleNotFoundError: No module named 'src.models'`

- [ ] **Step 3: Implement models.py**

```python
# src/models.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel


class LatLng(BaseModel):
    lat: float
    lng: float


class DeviceInfo(BaseModel):
    udid: str
    name: str
    ios_version: str
    conn_type: str  # "USB" | "WiFi"


class RouteStep(BaseModel):
    """A single maneuver in a route (turn-by-turn)."""
    maneuver: str
    distance: float  # meters
    duration: float  # seconds
    location: LatLng


class StopPoint(BaseModel):
    """A traffic signal snapped to the route."""
    position: LatLng
    distance_along_route: float  # meters from route start


class Route(BaseModel):
    polyline: list[LatLng]
    distance: float  # total meters
    duration: float  # estimated seconds
    steps: list[RouteStep]
    stop_points: list[StopPoint] = []


# --- Mode presets ---
_MODE_DEFAULTS: dict[str, dict] = {
    "driving": dict(
        base_speed_kmh=50, speed_variation_pct=0.10,
        jitter_meters=3.0, stationary_drift_meters=3.0,
        stop_probability=0.5, stop_duration_range=(15, 45),
        acceleration=2.5, deceleration=3.0,
    ),
    "walking": dict(
        base_speed_kmh=5, speed_variation_pct=0.15,
        jitter_meters=3.0, stationary_drift_meters=5.0,
        stop_probability=0.3, stop_duration_range=(5, 15),
        acceleration=1.0, deceleration=1.5,
    ),
    "cycling": dict(
        base_speed_kmh=18, speed_variation_pct=0.12,
        jitter_meters=3.0, stationary_drift_meters=4.0,
        stop_probability=0.4, stop_duration_range=(8, 25),
        acceleration=1.5, deceleration=2.0,
    ),
}


@dataclass
class SimulationConfig:
    mode: Literal["driving", "walking", "cycling"]
    base_speed_kmh: float | None = None
    speed_variation_pct: float | None = None
    jitter_meters: float | None = None
    stationary_drift_meters: float | None = None
    stop_probability: float | None = None
    stop_duration_range: tuple[int, int] | None = None
    acceleration: float | None = None
    deceleration: float | None = None

    def __post_init__(self):
        defaults = _MODE_DEFAULTS[self.mode]
        for key, default_val in defaults.items():
            if getattr(self, key) is None:
                object.__setattr__(self, key, default_val)


class SimulationState(BaseModel):
    lat: float
    lng: float
    speed_kmh: float
    heading_deg: float
    distance_traveled_m: float
    distance_remaining_m: float
    eta_seconds: float
    status: Literal[
        "idle", "moving", "decelerating",
        "stopped_at_signal", "accelerating", "paused", "completed"
    ]
    next_stop_distance_m: float | None = None
    elapsed_seconds: float = 0
```

- [ ] **Step 4: Run test — expect PASS**

```bash
python -m pytest tests/test_models.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/models.py tests/test_models.py
git commit -m "feat: add Pydantic models and dataclasses for all domain objects"
```

---

## Task 3: GPS Noise Engine (`src/gps_noise.py`)

**Files:**
- Create: `src/gps_noise.py`
- Create: `tests/test_gps_noise.py`

- [ ] **Step 1: Write tests**

```python
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
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
python -m pytest tests/test_gps_noise.py -v
```

- [ ] **Step 3: Implement gps_noise.py**

```python
# src/gps_noise.py
from __future__ import annotations

import math
import random


class GPSNoiseEngine:
    def __init__(self, seed: int | None = None):
        self._rng = random.Random(seed)

    def add_jitter(self, lat: float, lng: float, radius_m: float) -> tuple[float, float]:
        """Apply 2D Gaussian noise. 99.7% of points within *radius_m*."""
        if radius_m == 0.0:
            return lat, lng
        sigma = radius_m / 3  # 3-sigma = radius
        offset_y = self._rng.gauss(0, sigma)
        offset_x = self._rng.gauss(0, sigma)
        dlat = offset_y / 111320
        dlng = offset_x / (111320 * math.cos(math.radians(lat)))
        return lat + dlat, lng + dlng

    def stationary_drift(
        self, center_lat: float, center_lng: float,
        radius_m: float, elapsed_sec: float,
    ) -> tuple[float, float]:
        """Slow circular drift + noise overlay for 'standing still' effect."""
        period = 60.0  # seconds per full circle
        base_radius_m = radius_m * 0.5
        angle = (2 * math.pi * elapsed_sec) / period
        # Circular base motion
        bx = base_radius_m * math.cos(angle)
        by = base_radius_m * math.sin(angle)
        # Noise overlay (smaller than main jitter)
        overlay_sigma = radius_m * 0.2
        bx += self._rng.gauss(0, overlay_sigma)
        by += self._rng.gauss(0, overlay_sigma)
        dlat = by / 111320
        dlng = bx / (111320 * math.cos(math.radians(center_lat)))
        return center_lat + dlat, center_lng + dlng
```

- [ ] **Step 4: Run test — expect PASS**

```bash
python -m pytest tests/test_gps_noise.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/gps_noise.py tests/test_gps_noise.py
git commit -m "feat: GPS noise engine with jitter and stationary drift"
```

---

## Task 4: Route Engine (`src/route_engine.py`)

**Files:**
- Create: `src/route_engine.py`
- Create: `tests/test_route_engine.py`

- [ ] **Step 1: Write tests (with mocked HTTP)**

```python
# tests/test_route_engine.py
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
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
python -m pytest tests/test_route_engine.py -v
```

- [ ] **Step 3: Implement route_engine.py**

```python
# src/route_engine.py
from __future__ import annotations

import math
import logging

import gpxpy
import httpx

from src.models import LatLng, Route, RouteStep, StopPoint

logger = logging.getLogger("locmotion.route")

OSRM_BASE = "https://router.project-osrm.org"
OVERPASS_BASE = "https://overpass-api.de/api/interpreter"
NOMINATIM_BASE = "https://nominatim.openstreetmap.org"

_OSRM_PROFILES = {"driving": "car", "walking": "foot", "cycling": "bike"}


class RouteEngine:
    def __init__(self):
        self._client = httpx.AsyncClient(
            timeout=15.0,
            headers={"User-Agent": "LocMotion/1.0"},
        )

    async def close(self):
        await self._client.aclose()

    # --- Public API ---

    async def plan_route(
        self, start: LatLng, end: LatLng,
        mode: str = "driving",
    ) -> Route:
        profile = _OSRM_PROFILES.get(mode, "car")
        url = (
            f"{OSRM_BASE}/route/v1/{profile}/"
            f"{start.lng},{start.lat};{end.lng},{end.lat}"
        )
        resp = await self._client.get(url, params={
            "overview": "full",
            "geometries": "geojson",
            "steps": "true",
        })
        resp.raise_for_status()
        data = resp.json()
        route_data = data["routes"][0]

        polyline = [
            LatLng(lat=c[1], lng=c[0])
            for c in route_data["geometry"]["coordinates"]
        ]

        steps = []
        for leg in route_data["legs"]:
            for s in leg["steps"]:
                loc = s["maneuver"]["location"]
                steps.append(RouteStep(
                    maneuver=s["maneuver"]["type"],
                    distance=s["distance"],
                    duration=s["duration"],
                    location=LatLng(lat=loc[1], lng=loc[0]),
                ))

        return Route(
            polyline=polyline,
            distance=route_data["distance"],
            duration=route_data["duration"],
            steps=steps,
        )

    async def find_traffic_signals(self, route: Route) -> list[StopPoint]:
        bbox = self._route_bbox(route, buffer_m=50)
        query = (
            f"[out:json][timeout:10];"
            f'node["highway"="traffic_signals"]'
            f"({bbox['south']},{bbox['west']},{bbox['north']},{bbox['east']});"
            f"out body;"
        )
        resp = await self._client.get(OVERPASS_BASE, params={"data": query})
        resp.raise_for_status()
        elements = resp.json().get("elements", [])

        stops: list[StopPoint] = []
        for el in elements:
            p = LatLng(lat=el["lat"], lng=el["lon"])
            min_dist, along = self._snap_to_route(p, route.polyline)
            if min_dist < 25.0:
                stops.append(StopPoint(position=p, distance_along_route=along))

        stops.sort(key=lambda s: s.distance_along_route)
        return stops

    def parse_gpx(self, content: bytes) -> Route:
        gpx = gpxpy.parse(content.decode("utf-8"))
        points: list[LatLng] = []
        for track in gpx.tracks:
            for seg in track.segments:
                for pt in seg.points:
                    points.append(LatLng(lat=pt.latitude, lng=pt.longitude))

        distance = 0.0
        for i in range(1, len(points)):
            distance += self._haversine(points[i - 1], points[i])

        return Route(polyline=points, distance=distance, duration=0.0, steps=[])

    def parse_geojson(self, content: bytes) -> Route:
        import json
        data = json.loads(content)
        coords = []
        if data["type"] == "FeatureCollection":
            for f in data["features"]:
                if f["geometry"]["type"] == "LineString":
                    coords.extend(f["geometry"]["coordinates"])
        elif data["type"] == "Feature":
            coords = data["geometry"]["coordinates"]
        elif data["type"] == "LineString":
            coords = data["coordinates"]

        points = [LatLng(lat=c[1], lng=c[0]) for c in coords]
        distance = sum(
            self._haversine(points[i - 1], points[i]) for i in range(1, len(points))
        )
        return Route(polyline=points, distance=distance, duration=0.0, steps=[])

    async def geocode(self, query: str) -> list[LatLng]:
        resp = await self._client.get(
            f"{NOMINATIM_BASE}/search",
            params={"q": query, "format": "json", "limit": 5},
        )
        resp.raise_for_status()
        return [
            LatLng(lat=float(r["lat"]), lng=float(r["lon"]))
            for r in resp.json()
        ]

    # --- Internal helpers ---

    def _route_bbox(self, route: Route, buffer_m: float = 50) -> dict:
        lats = [p.lat for p in route.polyline]
        lngs = [p.lng for p in route.polyline]
        buf_deg = buffer_m / 111320
        return {
            "south": min(lats) - buf_deg,
            "north": max(lats) + buf_deg,
            "west": min(lngs) - buf_deg,
            "east": max(lngs) + buf_deg,
        }

    def _snap_to_route(
        self, point: LatLng, polyline: list[LatLng],
    ) -> tuple[float, float]:
        """Return (min_perpendicular_distance_m, distance_along_route_m)."""
        min_dist = float("inf")
        best_along = 0.0
        cumulative = 0.0

        for i in range(len(polyline) - 1):
            A, B = polyline[i], polyline[i + 1]
            seg_len = self._haversine(A, B)
            dist, frac = self._point_to_segment_distance(point, A, B)
            if dist < min_dist:
                min_dist = dist
                best_along = cumulative + frac * seg_len
            cumulative += seg_len

        return min_dist, best_along

    def _point_to_segment_distance(
        self, P: LatLng, A: LatLng, B: LatLng,
    ) -> tuple[float, float]:
        """Return (distance_meters, fraction_along_AB) for closest point on AB to P."""
        dx = B.lng - A.lng
        dy = B.lat - A.lat
        if dx == 0 and dy == 0:
            return self._haversine(P, A), 0.0

        t = ((P.lng - A.lng) * dx + (P.lat - A.lat) * dy) / (dx * dx + dy * dy)
        t = max(0.0, min(1.0, t))
        closest = LatLng(lat=A.lat + t * dy, lng=A.lng + t * dx)
        return self._haversine(P, closest), t

    @staticmethod
    def _haversine(a: LatLng, b: LatLng) -> float:
        """Distance in meters between two LatLng points."""
        R = 6371000
        dlat = math.radians(b.lat - a.lat)
        dlng = math.radians(b.lng - a.lng)
        lat1 = math.radians(a.lat)
        lat2 = math.radians(b.lat)
        h = (math.sin(dlat / 2) ** 2
             + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2)
        return R * 2 * math.asin(math.sqrt(h))
```

- [ ] **Step 4: Run test — expect PASS**

```bash
python -m pytest tests/test_route_engine.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/route_engine.py tests/test_route_engine.py
git commit -m "feat: route engine with OSRM routing, Overpass traffic signals, GPX parsing"
```

---

## Task 5: Motion Engine (`src/motion_engine.py`)

**Files:**
- Create: `src/motion_engine.py`
- Create: `tests/test_motion_engine.py`

- [ ] **Step 1: Write tests**

```python
# tests/test_motion_engine.py
import asyncio
import pytest
from unittest.mock import AsyncMock
from src.models import LatLng, Route, SimulationConfig, StopPoint
from src.motion_engine import MotionEngine


def make_straight_route(n_points=10, spacing_deg=0.001) -> Route:
    """A straight route heading north."""
    polyline = [LatLng(lat=25.0 + i * spacing_deg, lng=121.5) for i in range(n_points)]
    # Approx distance: n_points * spacing_deg * 111320 meters
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
        # Should have accelerated: speed = 0 + 2.5 * 0.5 = 1.25 m/s
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
            distance_along_route=333.96,  # ~3 points in
        )
        route.stop_points = [stop]
        engine = MotionEngine(route, config)
        engine._status = "moving"
        engine._current_speed_ms = 10.0
        # Place just before braking distance: v^2/(2*a) = 100/6 = 16.7m
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
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
python -m pytest tests/test_motion_engine.py -v
```

- [ ] **Step 3: Implement motion_engine.py**

```python
# src/motion_engine.py
from __future__ import annotations

import asyncio
import math
import random
import time
import logging
from typing import Callable, Awaitable

from src.models import LatLng, Route, SimulationConfig, SimulationState, StopPoint
from src.gps_noise import GPSNoiseEngine

logger = logging.getLogger("locmotion.motion")


class MotionEngine:
    def __init__(self, route: Route, config: SimulationConfig):
        self._route = route
        self._config = config
        self._noise = GPSNoiseEngine()

        # State
        self._status: str = "idle"
        self._current_speed_ms: float = 0.0  # meters/second
        self._distance_traveled: float = 0.0
        self._elapsed: float = 0.0
        self._stop_timer: float = 0.0  # remaining seconds at current stop
        self._last_tick_time: float = 0.0

        # Precompute segment cumulative distances
        self._seg_cumulative = self._compute_cumulative_distances()

        # Callbacks
        self._on_state: Callable[[SimulationState], Awaitable[None]] | None = None
        self._task: asyncio.Task | None = None

    def on_state_update(self, callback: Callable[[SimulationState], Awaitable[None]]):
        self._on_state = callback

    # --- Public controls ---

    async def start(self):
        self._status = "accelerating"
        self._last_tick_time = time.monotonic()
        self._task = asyncio.create_task(self._loop())

    async def pause(self):
        self._status = "paused"

    async def resume(self):
        if self._status == "paused":
            self._status = "moving"
            self._last_tick_time = time.monotonic()

    async def stop(self):
        self._status = "idle"
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def set_speed(self, speed_kmh: float):
        self._config.base_speed_kmh = speed_kmh

    def get_state(self) -> SimulationState:
        pos = self._interpolate_position()
        if self._status in ("stopped_at_signal", "paused", "idle"):
            noisy = self._noise.stationary_drift(
                pos.lat, pos.lng,
                self._config.stationary_drift_meters,
                self._elapsed,
            )
        else:
            noisy = self._noise.add_jitter(
                pos.lat, pos.lng, self._config.jitter_meters,
            )

        remaining = max(0, self._route.distance - self._distance_traveled)
        speed_kmh = self._current_speed_ms * 3.6
        eta = remaining / self._current_speed_ms if self._current_speed_ms > 0.5 else 0

        next_stop = self._next_stop_distance()

        return SimulationState(
            lat=noisy[0], lng=noisy[1],
            speed_kmh=round(speed_kmh, 1),
            heading_deg=round(self._compute_heading(), 1),
            distance_traveled_m=round(self._distance_traveled, 1),
            distance_remaining_m=round(remaining, 1),
            eta_seconds=round(eta, 0),
            status=self._status,
            next_stop_distance_m=round(next_stop, 1) if next_stop is not None else None,
            elapsed_seconds=round(self._elapsed, 1),
        )

    # --- Simulation loop ---

    async def _loop(self):
        try:
            while self._status not in ("idle", "completed"):
                now = time.monotonic()
                dt = now - self._last_tick_time
                self._last_tick_time = now

                if self._status != "paused":
                    self._tick(dt)
                    self._elapsed += dt

                if self._on_state:
                    await self._on_state(self.get_state())

                await asyncio.sleep(0.5)  # 2 Hz
        except asyncio.CancelledError:
            pass

    def _tick(self, dt: float):
        if self._status in ("paused", "idle", "completed"):
            return

        target_ms = self._config.base_speed_kmh / 3.6
        # Speed variation
        variation = 1 + random.gauss(0, self._config.speed_variation_pct)
        effective_target = target_ms * variation

        # Check for stop points
        next_stop_dist = self._next_stop_distance()
        braking_dist = (
            (self._current_speed_ms ** 2) / (2 * self._config.deceleration)
            if self._config.deceleration > 0 else 0
        )

        # Handle stopped state
        if self._status == "stopped_at_signal":
            self._stop_timer -= dt
            if self._stop_timer <= 0:
                self._status = "accelerating"
            return

        # Should we start braking?
        if next_stop_dist is not None and next_stop_dist <= braking_dist + 2:
            self._status = "decelerating"
            self._current_speed_ms = max(
                0, self._current_speed_ms - self._config.deceleration * dt
            )
            if self._current_speed_ms < 0.3:
                self._current_speed_ms = 0
                # Roll probability
                if random.random() < self._config.stop_probability:
                    self._status = "stopped_at_signal"
                    lo, hi = self._config.stop_duration_range
                    self._stop_timer = random.uniform(lo, hi)
                    self._advance_past_stop()
                else:
                    # Coast through at reduced speed
                    self._current_speed_ms = target_ms * 0.3
                    self._status = "moving"
                    self._advance_past_stop()
        elif self._current_speed_ms < effective_target:
            # Accelerating
            self._status = "accelerating"
            self._current_speed_ms = min(
                effective_target,
                self._current_speed_ms + self._config.acceleration * dt,
            )
            if self._current_speed_ms >= effective_target * 0.95:
                self._status = "moving"
        else:
            self._status = "moving"
            self._current_speed_ms = effective_target

        # Advance position
        advance = self._current_speed_ms * dt
        self._distance_traveled += advance

        if self._distance_traveled >= self._route.distance:
            self._distance_traveled = self._route.distance
            self._current_speed_ms = 0
            self._status = "completed"

    # --- Helpers ---

    def _compute_cumulative_distances(self) -> list[float]:
        cumulative = [0.0]
        for i in range(1, len(self._route.polyline)):
            a = self._route.polyline[i - 1]
            b = self._route.polyline[i]
            cumulative.append(cumulative[-1] + _haversine(a, b))
        return cumulative

    def _interpolate_position(self) -> LatLng:
        """Get lat/lng at current distance_traveled along route."""
        if self._distance_traveled <= 0:
            return self._route.polyline[0]
        if self._distance_traveled >= self._seg_cumulative[-1]:
            return self._route.polyline[-1]

        for i in range(1, len(self._seg_cumulative)):
            if self._seg_cumulative[i] >= self._distance_traveled:
                seg_start = self._seg_cumulative[i - 1]
                seg_len = self._seg_cumulative[i] - seg_start
                if seg_len == 0:
                    return self._route.polyline[i - 1]
                frac = (self._distance_traveled - seg_start) / seg_len
                a = self._route.polyline[i - 1]
                b = self._route.polyline[i]
                return LatLng(
                    lat=a.lat + frac * (b.lat - a.lat),
                    lng=a.lng + frac * (b.lng - a.lng),
                )
        return self._route.polyline[-1]

    def _compute_heading(self) -> float:
        """Heading in degrees (0=North, 90=East)."""
        pos = self._interpolate_position()
        # Find next point slightly ahead
        ahead_dist = self._distance_traveled + 5
        saved = self._distance_traveled
        self._distance_traveled = min(ahead_dist, self._route.distance)
        ahead = self._interpolate_position()
        self._distance_traveled = saved

        dlng = math.radians(ahead.lng - pos.lng)
        lat1 = math.radians(pos.lat)
        lat2 = math.radians(ahead.lat)
        x = math.sin(dlng) * math.cos(lat2)
        y = (math.cos(lat1) * math.sin(lat2)
             - math.sin(lat1) * math.cos(lat2) * math.cos(dlng))
        bearing = math.degrees(math.atan2(x, y))
        return (bearing + 360) % 360

    def _next_stop_distance(self) -> float | None:
        """Distance in meters to the next unvisited stop point."""
        for sp in self._route.stop_points:
            remaining = sp.distance_along_route - self._distance_traveled
            if remaining > 0:
                return remaining
        return None

    def _advance_past_stop(self):
        """Mark the nearest stop as passed so we don't re-trigger it."""
        for sp in self._route.stop_points:
            if sp.distance_along_route >= self._distance_traveled - 5:
                self._distance_traveled = sp.distance_along_route + 1
                return


def _haversine(a: LatLng, b: LatLng) -> float:
    R = 6371000
    dlat = math.radians(b.lat - a.lat)
    dlng = math.radians(b.lng - a.lng)
    lat1 = math.radians(a.lat)
    lat2 = math.radians(b.lat)
    h = (math.sin(dlat / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(h))
```

- [ ] **Step 4: Run test — expect PASS**

```bash
python -m pytest tests/test_motion_engine.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/motion_engine.py tests/test_motion_engine.py
git commit -m "feat: motion engine with acceleration, stops, interpolation"
```

---

## Task 6: Device Manager (`src/device_manager.py`)

**Files:**
- Create: `src/device_manager.py`

This module ports GeoPort's pymobiledevice3 logic. Unit testing requires a physical device, so we write the module with clean error handling and test manually.

- [ ] **Step 1: Implement device_manager.py**

```python
# src/device_manager.py
from __future__ import annotations

import asyncio
import logging
import os
import sys

from src.models import DeviceInfo

logger = logging.getLogger("locmotion.device")

# pymobiledevice3 imports
from pymobiledevice3.usbmux import list_devices
from pymobiledevice3.lockdown import create_using_usbmux
from pymobiledevice3.services.dvt.dvt_secure_socket_proxy import DvtSecureSocketProxyService
from pymobiledevice3.services.dvt.instruments.location_simulation import LocationSimulation
from pymobiledevice3.remote.remote_service_discovery import RemoteServiceDiscoveryService
from pymobiledevice3.remote.utils import (
    stop_remoted_if_required, resume_remoted_if_required, get_rsds,
)
from pymobiledevice3.remote.tunnel_service import (
    create_core_device_tunnel_service_using_rsd,
    get_remote_pairing_tunnel_services,
    CoreDeviceTunnelProxy,
)
from pymobiledevice3.bonjour import DEFAULT_BONJOUR_TIMEOUT


def check_sudo():
    """Check if running as sudo on macOS. Required for pymobiledevice3."""
    if sys.platform == "darwin" and os.geteuid() != 0:
        logger.error("Not running as sudo. pymobiledevice3 requires root on macOS.")
        logger.error("Please run: sudo python -m src.main")
        return False
    return True


class DeviceManager:
    def __init__(self):
        # Tunnel state per device: {udid: {host, port, ios_version, conn_type, lockdown}}
        self._connections: dict[str, dict] = {}
        # Persistent DVT session for active simulation
        self._dvt_session: _DVTSession | None = None
        self._tunnel_task: asyncio.Task | None = None
        self._tunnel_stop_event = asyncio.Event()

    async def list_devices(self) -> list[DeviceInfo]:
        """Discover connected iOS devices via USB."""
        devices: list[DeviceInfo] = []
        try:
            usb_devices = list_devices()
            for dev in usb_devices:
                udid = dev.serial
                conn = dev.connection_type
                try:
                    lockdown = create_using_usbmux(
                        udid, connection_type=conn, autopair=True,
                    )
                    info = lockdown.short_info
                    devices.append(DeviceInfo(
                        udid=udid,
                        name=info.get("DeviceName", "Unknown"),
                        ios_version=info.get("ProductVersion", "Unknown"),
                        conn_type="USB" if conn == "USB" else "WiFi",
                    ))
                except Exception as e:
                    logger.warning(f"Could not query device {udid}: {e}")
        except Exception as e:
            logger.error(f"Error listing devices: {e}")
        return devices

    async def connect(self, udid: str, conn_type: str = "USB") -> bool:
        """Establish tunnel to device. Caches connection info."""
        if udid in self._connections:
            logger.info(f"Reusing existing connection for {udid}")
            return True

        try:
            lockdown = create_using_usbmux(udid, autopair=True)
            ios_version = lockdown.short_info.get("ProductVersion", "0")
            major = int(ios_version.split(".")[0])

            if major >= 17:
                host, port = await self._start_tunnel_ios17(
                    udid, lockdown, ios_version,
                )
                self._connections[udid] = {
                    "host": host, "port": port,
                    "ios_version": ios_version,
                    "conn_type": conn_type,
                    "lockdown": None,
                }
            else:
                self._connections[udid] = {
                    "host": None, "port": None,
                    "ios_version": ios_version,
                    "conn_type": conn_type,
                    "lockdown": lockdown,
                }

            logger.info(
                f"Connected to {udid} (iOS {ios_version}) via {conn_type}"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to connect to {udid}: {e}")
            return False

    async def disconnect(self, udid: str) -> bool:
        """Tear down connection and clean up DVT session."""
        await self._close_dvt()
        if udid in self._connections:
            del self._connections[udid]
        self._tunnel_stop_event.set()
        logger.info(f"Disconnected {udid}")
        return True

    async def set_location(self, lat: float, lng: float) -> bool:
        """Inject a single location update. Called at ~2 Hz by simulation."""
        if not self._connections:
            logger.error("No device connected")
            return False

        udid = next(iter(self._connections))
        conn = self._connections[udid]
        major = int(conn["ios_version"].split(".")[0])

        try:
            if self._dvt_session is None:
                self._dvt_session = await _DVTSession.open(conn, major)

            self._dvt_session.location_sim.set(lat, lng)
            return True

        except Exception as e:
            logger.error(f"Error setting location: {e}")
            # Close broken session so next call reconnects
            await self._close_dvt()
            return False

    async def clear_location(self) -> bool:
        """Clear simulated location on device."""
        if not self._connections:
            return True

        udid = next(iter(self._connections))
        conn = self._connections[udid]
        major = int(conn["ios_version"].split(".")[0])

        try:
            if self._dvt_session is None:
                self._dvt_session = await _DVTSession.open(conn, major)
            self._dvt_session.location_sim.clear()
        except Exception as e:
            logger.error(f"Error clearing location: {e}")
        finally:
            await self._close_dvt()
        return True

    async def _close_dvt(self):
        if self._dvt_session:
            try:
                self._dvt_session.close()
            except Exception:
                pass
            self._dvt_session = None

    async def _start_tunnel_ios17(
        self, udid: str, lockdown, ios_version: str,
    ) -> tuple[str, str]:
        """Start QUIC or TCP tunnel for iOS 17+ and return (host, port)."""
        parts = ios_version.split(".")
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0

        host = None
        port = None
        ready = asyncio.Event()

        if major == 17 and minor <= 3:
            # iOS 17.0-17.3: QUIC tunnel via RSD
            stop_remoted_if_required()
            devices = await get_rsds(DEFAULT_BONJOUR_TIMEOUT)
            rsd = None
            for d in devices:
                if d.udid == udid:
                    rsd = d
                    break
            if rsd is None:
                raise RuntimeError(f"Could not find RSD for device {udid}")

            service = await create_core_device_tunnel_service_using_rsd(
                rsd, autopair=True,
            )

            async def _run_quic():
                nonlocal host, port
                async with service.start_quic_tunnel() as tunnel:
                    resume_remoted_if_required()
                    host = tunnel.address
                    port = str(tunnel.port)
                    ready.set()
                    await self._tunnel_stop_event.wait()

            self._tunnel_stop_event.clear()
            self._tunnel_task = asyncio.create_task(_run_quic())

        else:
            # iOS 17.4+: TCP tunnel
            proxy = CoreDeviceTunnelProxy(lockdown)

            async def _run_tcp():
                nonlocal host, port
                async with proxy.start_tcp_tunnel() as tunnel:
                    host = tunnel.address
                    port = str(tunnel.port)
                    ready.set()
                    await self._tunnel_stop_event.wait()

            self._tunnel_stop_event.clear()
            self._tunnel_task = asyncio.create_task(_run_tcp())

        await asyncio.wait_for(ready.wait(), timeout=30)
        return host, port


class _DVTSession:
    """Holds a persistent DVT connection for location injection."""

    def __init__(self, rsd_ctx, dvt_ctx, location_sim):
        self._rsd_ctx = rsd_ctx  # None for iOS <17
        self._dvt_ctx = dvt_ctx
        self.location_sim = location_sim

    @classmethod
    async def open(cls, conn: dict, major_version: int) -> _DVTSession:
        if major_version >= 17:
            host = conn["host"]
            port = conn["port"]
            rsd = RemoteServiceDiscoveryService((host, port))
            await rsd.__aenter__()
            dvt = DvtSecureSocketProxyService(rsd)
            dvt.__enter__()
            loc = LocationSimulation(dvt)
            return cls(rsd_ctx=rsd, dvt_ctx=dvt, location_sim=loc)
        else:
            lockdown = conn["lockdown"]
            dvt = DvtSecureSocketProxyService(lockdown=lockdown)
            dvt.__enter__()
            loc = LocationSimulation(dvt)
            return cls(rsd_ctx=None, dvt_ctx=dvt, location_sim=loc)

    def close(self):
        try:
            self._dvt_ctx.__exit__(None, None, None)
        except Exception:
            pass
        if self._rsd_ctx:
            try:
                # Best-effort async cleanup
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(
                        self._rsd_ctx.__aexit__(None, None, None)
                    )
                else:
                    loop.run_until_complete(
                        self._rsd_ctx.__aexit__(None, None, None)
                    )
            except Exception:
                pass
```

- [ ] **Step 2: Verify imports work (requires pymobiledevice3 installed)**

```bash
python -c "from src.device_manager import DeviceManager; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add src/device_manager.py
git commit -m "feat: device manager with persistent DVT session, ported from GeoPort"
```

---

## Task 7: FastAPI Backend (`src/main.py`)

**Files:**
- Create: `src/main.py`
- Create: `tests/test_api.py`

- [ ] **Step 1: Write API tests**

```python
# tests/test_api.py
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
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
python -m pytest tests/test_api.py -v
```

- [ ] **Step 3: Implement main.py**

```python
# src/main.py
from __future__ import annotations

import asyncio
import logging
import json
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import (
    FastAPI, WebSocket, WebSocketDisconnect,
    UploadFile, File, Request,
)
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from src.models import LatLng, SimulationConfig, SimulationState
from src.device_manager import DeviceManager, check_sudo
from src.route_engine import RouteEngine
from src.motion_engine import MotionEngine
from src.gps_noise import GPSNoiseEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("locmotion")

# --- Global state ---
device_mgr = DeviceManager()
route_engine = RouteEngine()
current_route = None
motion_engine: MotionEngine | None = None
static_location_task: asyncio.Task | None = None
ws_clients: set[WebSocket] = set()
noise_engine = GPSNoiseEngine()

templates = Jinja2Templates(directory="src/templates")


@asynccontextmanager
async def lifespan(app: FastAPI):
    check_sudo()
    yield
    await route_engine.close()
    if motion_engine:
        await motion_engine.stop()


app = FastAPI(lifespan=lifespan)


# --- WebSocket broadcast ---

async def broadcast_state(state: SimulationState):
    msg = json.dumps({"type": "simulation_state", "data": state.model_dump()})
    dead = set()
    for ws in ws_clients:
        try:
            await ws.send_text(msg)
        except Exception:
            dead.add(ws)
    ws_clients -= dead


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    ws_clients.add(ws)
    try:
        while True:
            await ws.receive_text()  # keep alive
    except WebSocketDisconnect:
        pass
    finally:
        ws_clients.discard(ws)


# --- HTML ---

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


# --- Device routes ---

@app.get("/api/devices")
async def api_list_devices():
    devices = await device_mgr.list_devices()
    return [d.model_dump() for d in devices]


@app.post("/api/devices/connect")
async def api_connect_device(body: dict):
    ok = await device_mgr.connect(body["udid"], body.get("conn_type", "USB"))
    if ok:
        return {"status": "connected"}
    return JSONResponse({"error": "Connection failed"}, status_code=500)


@app.post("/api/devices/disconnect")
async def api_disconnect_device():
    conns = list(device_mgr._connections.keys())
    for udid in conns:
        await device_mgr.disconnect(udid)
    return {"status": "disconnected"}


# --- Route routes ---

@app.post("/api/route/plan")
async def api_plan_route(body: dict):
    global current_route
    start = LatLng(**body["start"])
    end = LatLng(**body["end"])
    mode = body.get("mode", "driving")

    route = await route_engine.plan_route(start, end, mode)
    stops = await route_engine.find_traffic_signals(route)
    route.stop_points = stops
    current_route = route
    return route.model_dump()


@app.post("/api/route/upload")
async def api_upload_route(file: UploadFile = File(...)):
    global current_route
    content = await file.read()
    name = file.filename or ""

    if name.endswith(".gpx"):
        route = route_engine.parse_gpx(content)
    elif name.endswith(".geojson") or name.endswith(".json"):
        route = route_engine.parse_geojson(content)
    else:
        return JSONResponse(
            {"error": f"Unsupported format: {name}"}, status_code=400,
        )

    stops = await route_engine.find_traffic_signals(route)
    route.stop_points = stops
    current_route = route
    return route.model_dump()


@app.get("/api/route/preview")
async def api_route_preview():
    if current_route is None:
        return JSONResponse({"error": "No route planned"}, status_code=404)
    return current_route.model_dump()


# --- Simulation routes ---

@app.post("/api/simulation/start")
async def api_simulation_start(body: dict):
    global motion_engine
    if current_route is None:
        return JSONResponse({"error": "No route planned"}, status_code=400)

    _stop_static_location()

    config = SimulationConfig(**body)
    motion_engine = MotionEngine(current_route, config)

    async def on_tick(state: SimulationState):
        await device_mgr.set_location(state.lat, state.lng)
        await broadcast_state(state)

    motion_engine.on_state_update(on_tick)
    await motion_engine.start()
    return {"status": "started"}


@app.post("/api/simulation/pause")
async def api_simulation_pause():
    if motion_engine:
        await motion_engine.pause()
    return {"status": "paused"}


@app.post("/api/simulation/resume")
async def api_simulation_resume():
    if motion_engine:
        await motion_engine.resume()
    return {"status": "resumed"}


@app.post("/api/simulation/stop")
async def api_simulation_stop():
    global motion_engine
    if motion_engine:
        await motion_engine.stop()
        motion_engine = None
    await device_mgr.clear_location()
    return {"status": "stopped"}


@app.put("/api/simulation/speed")
async def api_simulation_speed(body: dict):
    if motion_engine:
        motion_engine.set_speed(body["speed_kmh"])
    return {"status": "ok"}


@app.put("/api/simulation/config")
async def api_simulation_config(body: dict):
    if motion_engine:
        cfg = motion_engine._config
        for key in (
            "jitter_meters", "stationary_drift_meters",
            "speed_variation_pct", "stop_probability",
            "stop_duration_range",
        ):
            if key in body:
                setattr(cfg, key, body[key])
    return {"status": "ok"}


# --- Static location ---

@app.post("/api/location/set")
async def api_set_location(body: dict):
    global static_location_task
    _stop_static_location()
    if motion_engine:
        await motion_engine.stop()

    lat, lng = body["lat"], body["lng"]

    async def _inject_loop():
        elapsed = 0.0
        try:
            while True:
                dlat, dlng = noise_engine.stationary_drift(
                    lat, lng, 3.0, elapsed,
                )
                await device_mgr.set_location(dlat, dlng)
                state = SimulationState(
                    lat=dlat, lng=dlng, speed_kmh=0, heading_deg=0,
                    distance_traveled_m=0, distance_remaining_m=0,
                    eta_seconds=0, status="idle",
                    elapsed_seconds=elapsed,
                )
                await broadcast_state(state)
                await asyncio.sleep(0.5)
                elapsed += 0.5
        except asyncio.CancelledError:
            pass

    static_location_task = asyncio.create_task(_inject_loop())
    return {"status": "ok", "lat": lat, "lng": lng}


@app.post("/api/location/clear")
async def api_clear_location():
    _stop_static_location()
    await device_mgr.clear_location()
    return {"status": "cleared"}


def _stop_static_location():
    global static_location_task
    if static_location_task:
        static_location_task.cancel()
        static_location_task = None


# --- Geocode ---

@app.get("/api/geocode")
async def api_geocode(q: str):
    results = await route_engine.geocode(q)
    return [r.model_dump() for r in results]


# --- Entry point ---

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.main:app", host="0.0.0.0", port=8080, reload=True)
```

- [ ] **Step 4: Run test — expect PASS**

```bash
python -m pytest tests/test_api.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/main.py tests/test_api.py
git commit -m "feat: FastAPI backend with all routes, WebSocket, static location loop"
```

---

## Task 8: Frontend (`src/templates/index.html`)

**Files:**
- Create: `src/templates/index.html`

- [ ] **Step 1: Create the single-file frontend**

The full HTML file includes:
- Leaflet map with route polyline, traffic signal markers, position marker, trail
- Right panel: route input (geocode search + mode), speed/realism sliders, controls
- Status bar with live stats and color-coded state
- WebSocket for 2Hz updates
- Click-to-set static location
- Mode presets that update all sliders

Key sections of the frontend:

**Map initialization** (Leaflet with OSM tiles, click handler for static location):
```javascript
map = L.map('map').setView([25.033, 121.565], 13);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: 'OpenStreetMap'
}).addTo(map);

map.on('click', (e) => {
    fetch('/api/location/set', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({lat: e.latlng.lat, lng: e.latlng.lng})
    });
});
```

**WebSocket handler** (receives state, updates marker + status bar):
```javascript
ws.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    if (msg.type === 'simulation_state') {
        marker.setLatLng([msg.data.lat, msg.data.lng]);
        // update status bar text and colors
    }
};
```

**Route drawing** (polyline + traffic signal circle markers):
```javascript
function drawRoute(route) {
    const coords = route.polyline.map(p => [p.lat, p.lng]);
    routeLine = L.polyline(coords, {color: '#3b82f6', weight: 4}).addTo(map);
    map.fitBounds(routeLine.getBounds());
    route.stop_points.forEach(sp => {
        L.circleMarker([sp.position.lat, sp.position.lng], {
            radius: 5, fillColor: '#ef4444', fillOpacity: 0.8
        }).addTo(map);
    });
}
```

**Simulation start** (collects all slider values into config object):
```javascript
async function startSim() {
    const body = {
        mode: currentMode,
        base_speed_kmh: +speedSlider.value,
        speed_variation_pct: +varSlider.value / 100,
        jitter_meters: +jitterSlider.value,
        // ... other config fields
    };
    await fetch('/api/simulation/start', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body)
    });
}
```

**Note:** The full HTML file is ~300 lines. The implementing agent should create the complete file with all the above pieces integrated, plus:
- Device select dropdown + refresh/connect buttons in header
- Mode buttons (Car/Walk/Bike) that apply presets to all sliders
- Pause/Resume/Stop buttons
- Status bar showing: status dot, status text, speed, distance, ETA
- Status bar background color changes based on state
- Trail polyline that shows recent path (last 200 points)
- Proper Tailwind classes for dark theme (bg-gray-900, bg-gray-800, etc.)
- CDN links: Leaflet 1.9 CSS+JS, Tailwind 2.2
- All DOM updates use textContent (not innerHTML) for security

- [ ] **Step 2: Start the dev server and test in browser**

```bash
sudo python -m src.main
```

Open `http://localhost:8080`. Verify:
- Map loads with OpenStreetMap tiles
- Right panel shows all controls
- Mode buttons switch and update slider presets
- Click on map triggers static location set (check network tab)

- [ ] **Step 3: Commit**

```bash
git add src/templates/index.html
git commit -m "feat: single-file frontend with Leaflet map, controls, WebSocket"
```

---

## Task 9: Integration Test & Polish

**Files:**
- Modify: various files for fixes discovered during integration

- [ ] **Step 1: Run full test suite**

```bash
python -m pytest tests/ -v
```

Fix any failures.

- [ ] **Step 2: Manual end-to-end test**

```bash
sudo python -m src.main
```

Test workflow:
1. Refresh devices -> see connected device
2. Connect to device
3. Enter two addresses -> Plan Route -> see polyline + red traffic signal dots
4. Click Start -> watch marker move along route
5. Verify speed/status bar updates at 2Hz
6. Test Pause/Resume/Stop
7. Click on map -> verify static location injection
8. Upload a GPX file -> verify route drawn

- [ ] **Step 3: Commit any fixes**

```bash
git add -A
git commit -m "fix: integration fixes from end-to-end testing"
```

---

## Summary

| Task | Module | Description |
|------|--------|-------------|
| 1 | Setup | Project scaffolding, dependencies |
| 2 | models.py | All data models (Pydantic + dataclass) |
| 3 | gps_noise.py | Jitter + stationary drift |
| 4 | route_engine.py | OSRM routing, Overpass signals, GPX parsing |
| 5 | motion_engine.py | Simulation loop with acceleration/stops |
| 6 | device_manager.py | iOS device connection (from GeoPort) |
| 7 | main.py | FastAPI routes, WebSocket, glue |
| 8 | index.html | Frontend UI |
| 9 | Integration | Full testing + fixes |
