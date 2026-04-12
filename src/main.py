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
    try:
        return templates.TemplateResponse(request, "index.html")
    except Exception as exc:
        logger.warning("index.html not found: %s", exc)
        return HTMLResponse("<h1>LocMotion</h1><p>Frontend not yet built.</p>", status_code=500)


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
