# LocMotion - Design Spec

## Overview

LocMotion is a realistic iOS GPS simulation tool with a web-based control panel. It provides navigation routing, speed simulation with acceleration/deceleration curves, GPS jitter, traffic signal stops, and real-time visualization — all designed to make simulated movement indistinguishable from real GPS behavior.

Device communication is built on pymobiledevice3 (core logic ported from GeoPort), with a custom high-frequency location injection loop that updates coordinates continuously rather than setting a static point.

## Architecture

```
┌──────────────────────────────────────────────────┐
│                  Frontend (Browser)                │
│  Leaflet Map + Vanilla JS + Tailwind CSS (CDN)    │
│                                                    │
│  ┌────────────┐ ┌──────────┐ ┌─────────────────┐  │
│  │ Map View   │ │ Route    │ │ Control Panel   │  │
│  │ - 路線預覽  │ │ Input    │ │ - 速度/抖動/停頓 │  │
│  │ - 即時位置  │ │ - 起終點  │ │ - 模式選擇      │  │
│  │ - 紅綠燈標記│ │ - GPX匯入│ │ - 即時狀態      │  │
│  └────────────┘ └──────────┘ └─────────────────┘  │
│                        │                           │
│                   WebSocket                        │
└────────────────────────┼───────────────────────────┘
                         │
┌────────────────────────┼───────────────────────────┐
│                  FastAPI Backend                    │
│                        │                           │
│  ┌─────────────────────┼─────────────────────────┐ │
│  │            Simulation Engine                   │ │
│  │                                                │ │
│  │  ┌──────────┐ ┌──────────┐ ┌───────────────┐  │ │
│  │  │ Route    │ │ Motion   │ │ GPS Noise     │  │ │
│  │  │ Engine   │ │ Engine   │ │ Engine        │  │ │
│  │  │          │ │          │ │               │  │ │
│  │  │ - OSRM   │ │ - 插值    │ │ - 高斯抖動    │  │ │
│  │  │ - Overpass│ │ - 加減速  │ │ - 靜止漂移    │  │ │
│  │  │ - 停頓點  │ │ - 停頓    │ │ - 訊號品質    │  │ │
│  │  │   計算    │ │ - 速度變異│ │               │  │ │
│  │  └──────────┘ └──────────┘ └───────────────┘  │ │
│  └────────────────────────────────────────────────┘ │
│                                                     │
│  ┌────────────────────────────────────────────────┐ │
│  │          Device Manager                        │ │
│  │  (ported from GeoPort's pymobiledevice3 logic) │ │
│  │                                                │ │
│  │  - 裝置發現 (USB / WiFi)                        │ │
│  │  - Tunnel 建立 (QUIC for iOS 17.4+)            │ │
│  │  - DVT LocationSimulation 高頻注入              │ │
│  └────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────┘
```

**Tech Stack:**
- Backend: FastAPI (Python 3.10+), asyncio, WebSocket
- Frontend: Vanilla JS, Leaflet.js, Tailwind CSS (CDN), no build step
- Device: pymobiledevice3
- Routing: OSRM public API
- Traffic data: Overpass API (OpenStreetMap)
- Port: 8080 (default)

## Module Design

### 1. Device Manager (`device_manager.py`)

Ported from GeoPort `main.py`. Responsible for device lifecycle.

**Interface:**

```python
class DeviceManager:
    async def list_devices() -> list[DeviceInfo]
    # Discover connected iOS devices via USB and WiFi.
    # Returns list of {udid, name, ios_version, conn_type}.

    async def connect(udid: str, conn_type: str) -> bool
    # Establish tunnel to device. Handles:
    # - iOS 17.0-17.3: WeTest driver (Windows) or native
    # - iOS 17.4+: QUIC tunnel
    # - iOS <17: TCP tunnel
    # Caches rsd_host/rsd_port for reuse.

    async def disconnect(udid: str) -> bool
    # Tear down tunnel and clean up.

    async def set_location(lat: float, lng: float) -> bool
    # Single location injection via DVT LocationSimulation.set().
    # NON-BLOCKING — sets once and returns immediately.
    # Called repeatedly by simulation engine at ~2 Hz.

    async def clear_location() -> bool
    # Clear simulated location via LocationSimulation.clear().
```

Key difference from GeoPort: `set_location` does NOT spin up a blocking thread. It opens a DVT session, calls `set()` once, and returns. The simulation engine calls it repeatedly.

**DVT session management:** Keep one persistent DVT connection open during simulation rather than reconnecting per call. Reconnect only on error.

### 2. Route Engine (`route_engine.py`)

Handles route planning and traffic signal discovery.

**Interface:**

```python
class RouteEngine:
    async def plan_route(
        start: LatLng,
        end: LatLng,
        mode: Literal["driving", "walking", "cycling"]
    ) -> Route
    # Calls OSRM API, returns Route with:
    # - polyline: list[LatLng] (dense coordinate list)
    # - distance: float (total meters)
    # - duration: float (estimated seconds)
    # - steps: list[RouteStep] (turn-by-turn maneuvers)

    async def find_traffic_signals(route: Route) -> list[StopPoint]
    # 1. Compute bounding box of route polyline + 50m buffer
    # 2. Query Overpass API:
    #    [out:json];
    #    node["highway"="traffic_signals"](bbox);
    #    out;
    # 3. For each signal node, compute perpendicular distance
    #    to nearest route segment
    # 4. If distance < 25m, snap to route and record:
    #    - distance_along_route (meters from start)
    #    - position (lat, lng snapped to polyline)
    # Returns sorted list by distance_along_route.

    def parse_gpx(file: UploadFile) -> Route
    def parse_kml(file: UploadFile) -> Route
    def parse_geojson(file: UploadFile) -> Route
    # Parse uploaded track files into Route objects.

    async def geocode(query: str) -> list[LatLng]
    # Address/place search using Nominatim API.
```

**Overpass query for route bbox:**
```
[out:json][timeout:10];
node["highway"="traffic_signals"]({south},{west},{north},{east});
out body;
```

**Snap-to-route algorithm:**
For each traffic signal point P, iterate through consecutive route segment pairs (A, B):
1. Project P onto line AB to get closest point Q
2. If Q is between A and B (not beyond endpoints), distance = |PQ|
3. Track minimum distance across all segments
4. If min distance < 25m, compute cumulative distance from route start to Q

### 3. Motion Engine (`motion_engine.py`)

Core simulation loop. Advances position along route with realistic physics.

**Interface:**

```python
class MotionEngine:
    def __init__(self, route: Route, config: SimulationConfig)

    async def start() -> None
    # Begin simulation loop. Runs at ~2 Hz (every 500ms).
    # Each tick:
    # 1. Compute elapsed time since last tick
    # 2. Apply acceleration/deceleration curve
    # 3. Check if approaching a stop point
    # 4. Advance position along route polyline
    # 5. Apply GPS noise
    # 6. Emit new position via callback

    async def pause() -> None
    async def resume() -> None
    async def stop() -> None

    def set_speed(speed_kmh: float) -> None
    # Change target speed mid-simulation.

    def get_state() -> SimulationState
    # Returns current position, speed, distance traveled,
    # distance remaining, ETA, current status.
```

**SimulationConfig:**
```python
@dataclass
class SimulationConfig:
    mode: Literal["driving", "walking", "cycling"]
    base_speed_kmh: float          # Target speed
    speed_variation_pct: float     # ±% random fluctuation (default 0.12)
    jitter_meters: float           # GPS noise radius (default 3.0)
    stationary_drift_meters: float # Drift when stopped (default 5.0)
    stop_probability: float        # Chance to stop at traffic signal (default 0.5)
    stop_duration_range: tuple[int, int]  # (min_sec, max_sec) (default (15, 45))
    acceleration: float            # m/s² (default 2.5 for driving)
    deceleration: float            # m/s² (default 3.0 for driving)
```

**Mode presets:**
| Parameter | Driving | Walking | Cycling |
|-----------|---------|---------|---------|
| base_speed_kmh | 50 | 5 | 18 |
| speed_variation_pct | 0.10 | 0.15 | 0.12 |
| jitter_meters | 3.0 | 3.0 | 3.0 |
| stationary_drift_meters | 3.0 | 5.0 | 4.0 |
| stop_probability | 0.5 | 0.3 | 0.4 |
| stop_duration_range | (15, 45) | (5, 15) | (8, 25) |
| acceleration (m/s²) | 2.5 | 1.0 | 1.5 |
| deceleration (m/s²) | 3.0 | 1.5 | 2.0 |

**Acceleration/deceleration curve:**
Use linear acceleration model (simple, predictable):
```
current_speed += acceleration * dt    (when accelerating)
current_speed -= deceleration * dt    (when braking)
current_speed = clamp(0, target_speed)
```

**Speed variation:**
Each tick, apply multiplicative noise to target speed:
```
effective_target = base_speed * (1 + random.gauss(0, speed_variation_pct))
```

**Stop behavior at traffic signals:**
When distance_to_next_stop < braking_distance:
1. Begin deceleration
2. At stop point: roll probability (e.g. 50%)
   - Stop triggered: hold position for random duration within range, apply stationary drift
   - No stop: coast through at reduced speed (~30% of base)
3. After stop duration: accelerate back to target speed

**Braking distance formula:**
```
braking_distance = current_speed² / (2 * deceleration)
```

### 4. GPS Noise Engine (`gps_noise.py`)

Adds realistic GPS inaccuracy to coordinates.

**Interface:**

```python
class GPSNoiseEngine:
    def add_jitter(lat: float, lng: float, radius_m: float) -> tuple[float, float]
    # Apply 2D Gaussian noise.
    # offset_x = random.gauss(0, radius_m / 3)  # 99.7% within radius
    # offset_y = random.gauss(0, radius_m / 3)
    # Convert meter offsets to lat/lng deltas:
    #   dlat = offset_y / 111320
    #   dlng = offset_x / (111320 * cos(radians(lat)))

    def stationary_drift(
        center_lat: float, center_lng: float,
        radius_m: float, elapsed_sec: float
    ) -> tuple[float, float]
    # Slow circular drift pattern with noise overlay.
    # Base: circular motion with period ~60s, radius ~2-3m
    # Overlay: Gaussian jitter ±1-2m
    # Creates realistic "GPS wandering while standing still" effect.
```

### 5. WebSocket State Broadcasting

The simulation engine broadcasts state to frontend at 2 Hz via WebSocket.

**Message format:**
```json
{
    "type": "simulation_state",
    "data": {
        "lat": 25.0330,
        "lng": 121.5654,
        "speed_kmh": 42.3,
        "heading_deg": 187.5,
        "distance_traveled_m": 1523,
        "distance_remaining_m": 3477,
        "eta_seconds": 298,
        "status": "moving",
        "next_stop_distance_m": 312,
        "elapsed_seconds": 187
    }
}
```

**Status values:** `idle` | `moving` | `decelerating` | `stopped_at_signal` | `accelerating` | `paused` | `completed`

### 6. FastAPI Routes

```
GET  /                          → Serve control panel HTML
GET  /api/devices               → List connected iOS devices
POST /api/devices/connect       → Connect to device {udid, conn_type}
POST /api/devices/disconnect    → Disconnect device

POST /api/route/plan            → Plan route {start, end, mode}
POST /api/route/upload          → Upload GPX/KML/GeoJSON file
GET  /api/route/preview         → Get current route polyline + stop points

POST /api/simulation/start      → Start simulation {config}
POST /api/simulation/pause      → Pause
POST /api/simulation/resume     → Resume
POST /api/simulation/stop       → Stop and clear location

PUT  /api/simulation/speed      → Change speed mid-simulation
PUT  /api/simulation/config     → Update jitter/stop params mid-simulation

POST /api/location/set          → Set static location {lat, lng} (with jitter)
POST /api/location/clear        → Clear simulated location

GET  /api/geocode?q=...         → Address search

WS   /ws                        → WebSocket for real-time state updates
```

## Frontend Design

Single HTML file served by FastAPI. Layout:

```
┌─────────────────────────────────────────────────────────┐
│  LocMotion                           [Device ▾] [Connect]│
├────────────────────────────────┬────────────────────────┤
│                                │  Route                  │
│                                │  ┌──────────────────┐  │
│                                │  │ From: [搜尋地址]   │  │
│                                │  │ To:   [搜尋地址]   │  │
│                                │  │ Mode: 🚗 🚶 🚲     │  │
│                                │  │ [Plan Route]      │  │
│                                │  │ [Upload GPX/KML]  │  │
│         Map (Leaflet)          │  └──────────────────┘  │
│                                │                        │
│  - Route polyline (blue)       │  Speed & Motion         │
│  - Current position (marker)   │  ┌──────────────────┐  │
│  - Traffic signals (red dots)  │  │ Speed: [===●===] km/h│
│  - Stop events (animated)      │  │ Accel: [===●===]  │  │
│                                │  │ Decel: [===●===]  │  │
│                                │  └──────────────────┘  │
│                                │                        │
│                                │  Realism                │
│                                │  ┌──────────────────┐  │
│                                │  │ Jitter:  [==●==] m│  │
│                                │  │ Drift:   [==●==] m│  │
│                                │  │ Speed ±: [==●==] %│  │
│                                │  │ Stop %:  [==●==]  │  │
│                                │  │ Stop time: 15-45s │  │
│                                │  └──────────────────┘  │
├────────────────────────────────┴────────────────────────┤
│  Status Bar                                              │
│  ● Moving 42.3 km/h | 1.5km / 5.0km | ETA 4:58 | ⏸ ⏹  │
└─────────────────────────────────────────────────────────┘
```

**Map interactions:**
- Click to set static location (with jitter applied)
- Route preview: polyline + traffic signal markers + stop points
- Real-time marker animation during simulation
- Marker trail showing recent path (fades out)

**Real-time updates:**
- WebSocket receives state at 2 Hz
- Map marker position updates smoothly (interpolation between updates)
- Speed gauge, distance, ETA update live
- Status bar color reflects state (green=moving, yellow=decelerating, red=stopped)

## Project Structure

```
LocMotion/
├── docs/
│   └── specs/
│       └── 2026-04-12-locmotion-design.md    # This file
├── src/
│   ├── main.py                # FastAPI app entry, routes, WebSocket
│   ├── device_manager.py      # iOS device connection (from GeoPort)
│   ├── route_engine.py        # OSRM routing + Overpass traffic signals
│   ├── motion_engine.py       # Simulation loop, acceleration, stops
│   ├── gps_noise.py           # Jitter + stationary drift
│   ├── models.py              # Pydantic models (DeviceInfo, Route, etc.)
│   └── templates/
│       └── index.html         # Control panel UI (single file)
├── requirements.txt
└── README.md
```

## External API Dependencies

| API | Purpose | Rate Limit | Auth |
|-----|---------|------------|------|
| OSRM (router.project-osrm.org) | Route planning | Reasonable use, no hard limit | None |
| Overpass API (overpass-api.de) | Traffic signal nodes | ~10k requests/day | None |
| Nominatim (nominatim.openstreetmap.org) | Geocoding (address search) | 1 req/sec | None, requires User-Agent |

All three are free, no API keys needed.

## Key Design Decisions

1. **Non-blocking location injection:** Unlike GeoPort's blocking thread, we call `LocationSimulation.set()` repeatedly from an async loop. This enables smooth continuous movement.

2. **Persistent DVT session:** Keep one DVT connection open for the duration of simulation rather than reconnecting per update. Reconnect on error with exponential backoff.

3. **Server-side simulation loop:** The simulation runs on the backend (asyncio), not in the browser. Browser tabs get throttled; backend async tasks don't.

4. **2 Hz update rate:** Update device location every 500ms. Fast enough for smooth movement, slow enough to avoid overwhelming the DVT connection.

5. **All-in-one HTML frontend:** Single file, CDN dependencies, no build step. Matches the simplicity of GeoPort's approach and keeps deployment trivial.

6. **OSRM + Overpass combination:** Route geometry from OSRM, traffic signals from Overpass. Both use OSM data so coordinate systems align naturally. Query traffic signals once at route planning time, not during simulation.
