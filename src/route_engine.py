from __future__ import annotations

import math
import logging

import gpxpy
import httpx

from src.models import LatLng, Route, RouteStep, StopPoint

logger = logging.getLogger("locmotion.route")

# OSRM mirrors — tuples of (base_url, profile_format)
# project-osrm uses /route/v1/{profile}/ where profile is car/foot/bike
# openstreetmap.de uses /routed-{profile}/route/v1/driving/ where profile is car/foot/bike
OSRM_ENDPOINTS = [
    ("https://router.project-osrm.org", "project_osrm"),
    ("https://routing.openstreetmap.de", "osm_de"),
]
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
]
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

    async def plan_route(
        self, start: LatLng, end: LatLng,
        mode: str = "driving",
        waypoints: list[LatLng] | None = None,
    ) -> Route:
        profile = _OSRM_PROFILES.get(mode, "car")
        points = [start] + (waypoints or []) + [end]
        coords_str = ";".join(f"{p.lng},{p.lat}" for p in points)
        data = await self._osrm_query(profile, coords_str)
        if data is None:
            raise RuntimeError("所有 OSRM endpoint 都無法使用，請稍後再試")
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
            f"[out:json][timeout:25];"
            f'node["highway"="traffic_signals"]'
            f"({bbox['south']},{bbox['west']},{bbox['north']},{bbox['east']});"
            f"out body;"
        )
        elements = await self._overpass_query(query)
        if elements is None:
            return []

        stops: list[StopPoint] = []
        for el in elements:
            p = LatLng(lat=el["lat"], lng=el["lon"])
            min_dist, along = self._snap_to_route(p, route.polyline)
            if min_dist < 25.0:
                stops.append(StopPoint(position=p, distance_along_route=along))

        stops.sort(key=lambda s: s.distance_along_route)
        return stops

    async def _osrm_query(self, profile: str, coords_str: str) -> dict | None:
        """Run OSRM route query with retry + mirror fallback."""
        import asyncio as _asyncio
        params = {"overview": "full", "geometries": "geojson", "steps": "true"}
        last_err = None
        for attempt in range(2):
            for base, kind in OSRM_ENDPOINTS:
                if kind == "project_osrm":
                    url = f"{base}/route/v1/{profile}/{coords_str}"
                else:  # osm_de
                    url = f"{base}/routed-{profile}/route/v1/driving/{coords_str}"
                try:
                    resp = await self._client.get(url, params=params, timeout=30.0)
                    if resp.status_code == 200:
                        data = resp.json()
                        if data.get("code") == "Ok" and data.get("routes"):
                            return data
                        last_err = f"{base} -> code={data.get('code')}"
                    else:
                        last_err = f"{base} -> HTTP {resp.status_code}"
                    logger.debug(f"OSRM {last_err}")
                except Exception as e:
                    last_err = f"{base} -> {e}"
                    logger.debug(f"OSRM {last_err}")
            if attempt == 0:
                await _asyncio.sleep(1.0)
        logger.error(f"All OSRM endpoints failed. Last: {last_err}")
        return None

    async def _overpass_query(self, query: str) -> list | None:
        """Run Overpass query with retry + mirror fallback. Returns elements or None on total failure."""
        import asyncio as _asyncio
        last_err = None
        for attempt in range(2):  # try each endpoint up to 2 times
            for endpoint in OVERPASS_ENDPOINTS:
                try:
                    resp = await self._client.get(
                        endpoint, params={"data": query}, timeout=30.0,
                    )
                    if resp.status_code == 200:
                        return resp.json().get("elements", [])
                    last_err = f"{endpoint} -> {resp.status_code}"
                    logger.debug(f"Overpass {last_err}")
                except Exception as e:
                    last_err = f"{endpoint} -> {e}"
                    logger.debug(f"Overpass {last_err}")
            if attempt == 0:
                await _asyncio.sleep(1.0)  # brief backoff before retry round
        logger.warning(f"All Overpass endpoints failed, skipping traffic signals. Last: {last_err}")
        return None

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

    async def geocode_search(self, query: str) -> list[dict]:
        """Geocode with display names for autocomplete UI."""
        resp = await self._client.get(
            f"{NOMINATIM_BASE}/search",
            params={"q": query, "format": "json", "limit": 5},
        )
        resp.raise_for_status()
        return [
            {"lat": float(r["lat"]), "lng": float(r["lon"]),
             "display_name": r.get("display_name", "")}
            for r in resp.json()
        ]

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
        R = 6371000
        dlat = math.radians(b.lat - a.lat)
        dlng = math.radians(b.lng - a.lng)
        lat1 = math.radians(a.lat)
        lat2 = math.radians(b.lat)
        h = (math.sin(dlat / 2) ** 2
             + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2)
        return R * 2 * math.asin(math.sqrt(h))
