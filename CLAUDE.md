# CLAUDE.md

Guidance for Claude when working on the LocMotion codebase.

## Project Overview

LocMotion 是一個 macOS 上的 iOS GPS 模擬工具，用 FastAPI + pymobiledevice3 實現。特色：
- 2Hz 高頻座標注入，產生連續移動效果（不是設定靜止點）
- 支援路線規劃、加減速模型、紅綠燈停頓、GPS 抖動等擬真特性
- Web 控制台（Leaflet 地圖 + Tailwind），單檔 HTML 無 build step

核心設計脫胎自 GeoPort（位於 `../GeoPort`），但 pymobiledevice3 的使用方式不同：GeoPort 每次 `set_location` 都重開 DVT session，LocMotion 保持**持久 DVT session** 以支援 2Hz 連續注入。

## Architecture

```
┌─ Frontend (src/templates/index.html) ──────────────────────┐
│  Leaflet + Tailwind + Vanilla JS，透過 WebSocket 接收狀態    │
└─────────┬───────────────────────────────────────────────────┘
          │ HTTP + WS
┌─────────▼────────────────────────────────────────────────────┐
│  FastAPI (src/main.py)                                        │
│  ├ device_manager  ── 裝置連線（持久 DVT）                    │
│  ├ route_engine    ── OSRM 路線 + Overpass 紅綠燈 + GPX       │
│  ├ motion_engine   ── 模擬 loop（加減速、停頓、插值）          │
│  └ gps_noise       ── 抖動 + 靜止漂移                          │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼ pymobiledevice3 DVT
                          iOS Device
```

## Module Responsibilities

| 檔案 | 職責 | 可單獨測試？ |
|------|------|------------|
| `src/models.py` | Pydantic models + `SimulationConfig` dataclass（含 mode presets） | ✓ |
| `src/gps_noise.py` | Gaussian 抖動、圓周漂移 | ✓ |
| `src/route_engine.py` | OSRM + Overpass + Nominatim + GPX 解析；含 mirror + retry | ✓ (mock HTTP) |
| `src/motion_engine.py` | 模擬 loop、狀態機、位置插值、方位計算 | ✓ |
| `src/device_manager.py` | 裝置列舉、tunnel 建立、持久 DVT session | ✗ (需實體裝置) |
| `src/main.py` | FastAPI routes、WebSocket broadcast、全域狀態 | 部分（mock device_mgr） |
| `src/templates/index.html` | 單檔前端 | 手動瀏覽器測試 |

## Key Design Decisions（不要變動，除非用戶明確要求）

1. **macOS only** — 不支援 Windows/Linux。GeoPort 的 `pyuac`/`icacls`/WeTest driver 等邏輯已刻意移除。
2. **持久 DVT session** — `device_manager._DVTSession` 一次開啟、重複使用。錯誤時才 reconnect。原因：2Hz 注入下每次重建 session 會撞到 `Connection reset by peer`。
3. **2Hz 注入** — `motion_engine._loop` 固定 `asyncio.sleep(0.5)`。在 iOS 上不會太頻繁但能產生連續移動觀感。
4. **靜態定位也用 2Hz loop** — `main.py:api_set_location` 啟一個 asyncio task，持續注入帶漂移的座標。iOS 若太久沒更新會回到真實位置。
5. **sudo 必需** — pymobiledevice3 在 macOS 上需要 root。啟動時 `check_sudo()` 會警告。
6. **所有外部 API 有 mirror + retry** — OSRM 和 Overpass 都有備用 endpoint，各 2 輪重試。Nominatim 沒有（geocode 失敗就失敗，UX 上可接受）。

## Dev Workflow

```bash
# 虛擬環境（使用 uv）
uv venv .venv
source .venv/bin/activate
uv pip install -r requirements.txt

# 測試
python -m pytest tests/ -v

# 執行（macOS 需 sudo）
sudo .venv/bin/python -m src.main
# 訪問 http://localhost:8080
```

測試執行注意：subagent 環境不能 `source`，請直接用 `.venv/bin/python -m pytest`。

`uvicorn.run` 帶 `reload_dirs=["src"]`（見 `main.py`）— 在 `.worktrees/` 裡改檔案不會觸發主目錄 server 重啟。別拿掉。

## pymobiledevice3 版本相容性注意事項

這個函式庫的 API 不太穩定，實作中踩過的坑：

- **`CoreDeviceTunnelProxy`** 新版簽名是 `(service, remote_identifier)`，不能直接 `CoreDeviceTunnelProxy(lockdown)`。要用 classmethod `await CoreDeviceTunnelProxy.create(lockdown)`。
- **`LocationSimulation.set(lat, lng)`** 不支援 speed/course，所以 `CLLocation.speed` 在 iOS 端會是 `-1`。靠 `CLLocation.speed` 的 App 看不到速度，但從位置差分計算速度的 App 可以。這是 Apple DVT 協議限制，不是我們能改的。
- **`DvtProvider`** 取代了舊的 `DvtSecureSocketProxyService`（subagent 移植時自動適配過）。

更新 pymobiledevice3 前先跑 `python -c "from src.device_manager import DeviceManager"` 確認 import 不爆。

## External API Notes

- **OSRM** (`router.project-osrm.org`) — 公開 demo，時常慢或 503。備用：`routing.openstreetmap.de`（URL 格式不同：`/routed-{car,foot,bike}/route/v1/driving/...`）。
- **Overpass** (`overpass-api.de`) — 非常不穩定，504 Gateway Timeout 很常見。有 3 個 mirror。查詢 timeout 設在 25s。
- **Nominatim** (`nominatim.openstreetmap.org`) — 要求帶 User-Agent（我們用 `"LocMotion/1.0"`）、速率 1 req/sec。

## Testing Conventions

- 用 TDD 開發：先寫失敗測試、實作、確認通過、commit
- HTTP 呼叫一律 mock（見 `tests/test_route_engine.py` 的 `make_mock_response`）
- Motion engine 的 tick 直接呼叫 `engine._tick(dt=0.5)`，不走 async loop，速度快且可控
- Device manager 不寫 unit test（需實體裝置）

## Known Workflows

### 前端互動設計
- **單擊地圖** → 500ms 延遲後加路線點（dblclick 會取消）
- **雙擊地圖** → 設定靜態定位（需已連線裝置）
- **從目前位置出發** checkbox → 用 `lastSimPosition` **取代**起點（points >= 2 時會先 shift 掉舊起點再 unshift），支援 A→B→C 接續導航
- **地圖點優先於文字框** → `routePoints.length >= 1` 時，`fromInput`/`toInput` 整個被忽略並在 UI disable 反灰；沒有地圖點時才吃文字框
- **紅綠燈合併** → `route_engine.find_traffic_signals` 會把沿路線 `SIGNAL_MERGE_DISTANCE_M` (40m) 內的 signals collapse 成單一 `StopPoint`，避免大路口連續停等
- **routeVersion 追蹤** → 確保模擬中改路線後，按「開始」會重啟而非 resume

### 狀態機
`idle → accelerating → moving → decelerating → stopped_at_signal → accelerating → ... → completed`
paused 可從任何 moving 狀態進入，resume 會回到 moving。
loop 模式下 completed 會 reset 回 accelerating。

## What Not to Do

- ❌ 加回 Windows/Linux 相容性程式碼（之前刻意移除）
- ❌ 改回每次 `set_location` 開新 DVT session（會撞 connection reset）
- ❌ 為「發布準備」在程式碼加任何 telemetry、analytics、crash reporter
- ❌ 在前端加 innerHTML（Security hook 會擋，改用 textContent + createElement）
- ❌ 試圖注入 speed/course 到 `LocationSimulation`（協議不支援）
