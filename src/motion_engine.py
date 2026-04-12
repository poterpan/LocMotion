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
        self._current_speed_ms: float = 0.0
        self._distance_traveled: float = 0.0
        self._elapsed: float = 0.0
        self._stop_timer: float = 0.0
        self._last_tick_time: float = 0.0
        self._loop_count: int = 0

        self._seg_cumulative = self._compute_cumulative_distances()

        self._on_state: Callable[[SimulationState], Awaitable[None]] | None = None
        self._task: asyncio.Task | None = None

    def on_state_update(self, callback: Callable[[SimulationState], Awaitable[None]]):
        self._on_state = callback

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

                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            pass

    def _tick(self, dt: float):
        if self._status in ("paused", "idle", "completed"):
            return

        target_ms = self._config.base_speed_kmh / 3.6
        variation = 1 + random.gauss(0, self._config.speed_variation_pct)
        effective_target = target_ms * variation

        next_stop_dist = self._next_stop_distance()
        braking_dist = (
            (self._current_speed_ms ** 2) / (2 * self._config.deceleration)
            if self._config.deceleration > 0 else 0
        )

        if self._status == "stopped_at_signal":
            self._stop_timer -= dt
            if self._stop_timer <= 0:
                self._status = "accelerating"
            return

        if next_stop_dist is not None and next_stop_dist <= braking_dist + 2:
            self._status = "decelerating"
            self._current_speed_ms = max(
                0, self._current_speed_ms - self._config.deceleration * dt
            )
            if self._current_speed_ms < 0.3:
                self._current_speed_ms = 0
                if random.random() < self._config.stop_probability:
                    self._status = "stopped_at_signal"
                    lo, hi = self._config.stop_duration_range
                    self._stop_timer = random.uniform(lo, hi)
                    self._advance_past_stop()
                else:
                    self._current_speed_ms = target_ms * 0.3
                    self._status = "moving"
                    self._advance_past_stop()
        elif self._current_speed_ms < effective_target:
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

        advance = self._current_speed_ms * dt
        self._distance_traveled += advance

        if self._distance_traveled >= self._route.distance:
            if self._config.loop:
                # Reset to start for next lap
                self._distance_traveled = 0.0
                self._current_speed_ms = 0.0
                self._status = "accelerating"
                self._loop_count += 1
                logger.info(f"Loop #{self._loop_count} starting")
            else:
                self._distance_traveled = self._route.distance
                self._current_speed_ms = 0
                self._status = "completed"

    def _compute_cumulative_distances(self) -> list[float]:
        cumulative = [0.0]
        for i in range(1, len(self._route.polyline)):
            a = self._route.polyline[i - 1]
            b = self._route.polyline[i]
            cumulative.append(cumulative[-1] + _haversine(a, b))
        return cumulative

    def _interpolate_position(self) -> LatLng:
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
        pos = self._interpolate_position()
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
        for sp in self._route.stop_points:
            remaining = sp.distance_along_route - self._distance_traveled
            if remaining > 0:
                return remaining
        return None

    def _advance_past_stop(self):
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
