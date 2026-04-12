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
