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
