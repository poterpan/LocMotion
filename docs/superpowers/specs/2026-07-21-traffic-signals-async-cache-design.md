# 紅綠燈背景載入 + bbox 快取 + 🚦 marker

日期：2026-07-21
狀態：設計已確認，待實作

## 問題

使用者回報「規劃路線常常要等好久」，直覺認為是路線規劃 API（OSRM）太慢，想換掉。

## 根因（實測）

實測台北車站→台北101 一段路線，兩輪：

| 元件 | Run 1 | Run 2 | 狀態 |
|------|-------|-------|------|
| OSRM project_osrm | 1027ms | 1234ms | ✓ 正常 |
| OSRM osm_de（備援） | 1074ms | 1049ms | ✓ 正常 |
| Overpass overpass-api.de（主要） | 6315ms | 18757ms | ⚠️ 慢且不穩 |
| Overpass kumi.systems（備援1） | 35s timeout | 35s timeout | ✗ 掛 |
| Overpass osm.ch（備援2） | 1011ms 回 0 筆 | 1070ms 回 0 筆 | ✗ 資料不完整 |
| Nominatim | 1053ms | 251ms | ✓ 正常 |

**結論：OSRM（路線規劃）不是瓶頸，~1s 就回。** 真正拖慢的是規劃完之後串行執行的
`find_traffic_signals`（Overpass 查沿路紅綠燈），主要 endpoint 每次要 6~19s，且三個
mirror 實際只有 `overpass-api.de` 回正確資料、偏偏就是慢的那個，所以「並發搶最快 mirror」
也救不了。使用者按下「規劃路線」到看到結果，85~95% 時間花在等紅綠燈資料。

換 OSRM 不會解決問題。真正的解法在紅綠燈這一段。

`api_plan_route`（main.py:153-154）與 `api_upload_route`（main.py:175）都會同步等
Overpass，並把 `stop_points` 塞進 `current_route`；`current_route.stop_points` 同時
餵前端 marker 與 motion engine 停等點，兩邊都要顧。

## 設計

### ① 後端 — 解耦（讓路線秒出）

- `POST /api/route/plan`、`POST /api/route/upload` **不再**呼叫 `find_traffic_signals`。
  規劃/解析完就把 `current_route`（`stop_points=[]`）存好回傳 → 路線 ~1s 秒回。
- 新增 `GET /api/route/traffic_signals`：對 `current_route` 跑 `find_traffic_signals`
  （走快取），寫回 `current_route.stop_points`，回傳 `{"stop_points": [...]}`。
  `current_route is None` → HTTP 404。

### ② 後端 — bbox 記憶體快取（route_engine）

- `find_traffic_signals` 內加記憶體快取：
  - 把 route bbox 往外對齊到 **~0.01°（約 1.1km）網格**，用對齊後的 4-tuple 當 key。
  - 快取存 Overpass 回來的**原始 signal 節點**（非最終 stops），因為 stops 要依各路線
    polyline 重新 snap（本地便宜運算）。
  - key 命中 → 跳過 Overpass 直接用快取節點；未命中才打 Overpass 並寫入快取。
- **不設 TTL**：紅綠燈是道路基礎設施，位置基本不變。快取活在 server process 生命週期內，
  重啟自然清空（也順便處理極少數「路口改建」與「壞快取」情況）。
- 加 **max entries 上限（256 個區域）** 當記憶體保險，滿了淘汰最舊的。
- **決策紀錄**：不做磁碟持久化。理由：(a) 效益小——記憶體快取已消除同 session 內所有重複
  等待，磁碟只多省「重啟後每區第一次抓取」；(b) footgun——Overpass 會回 200 但空/不完整
  （實測 osm.ch 回 0 筆），永久磁碟檔一旦存到壞資料，該區域永遠沒紅綠燈直到手動刪檔；
  (c) 磁碟版需版本號 + 壞檔 fallback + 清快取端點，複雜度不划算。日後若需要，換快取後端是
  隔離的小升級，現在延後零成本。

### ③ 前端 — 背景載入 + 開始把關

- `drawRoute` 先畫 polyline（stops 可能為空）。
- plan/upload 成功後呼叫 `loadTrafficSignals()`：顯示「紅綠燈載入中…」→ fetch 新端點 →
  回來畫 🚦 marker、更新狀態；把 promise 存成 `signalsPromise`。
- 「開始」按鈕先 `await signalsPromise`（若還在跑），確保後端 `current_route.stop_points`
  已填好才送 start，模擬停等點不會漏。

### ④ 前端 — 🚦 marker

- 現在的紅色 `circleMarker`（index.html:899）換成 `L.divIcon` 的 🚦 emoji，沿用現有
  `makePointIcon` 的 divIcon 寫法（符合「不用 element.innerHTML」規範——Leaflet divIcon
  的 html 參數非 DOM innerHTML，且 makePointIcon 已在用）。

## 機制取捨

選「前端兩段式呼叫」而非「後端背景 task + WebSocket 推播」：純 HTTP、好單元測試、
start 把關單純（await promise）、不用新增 WS 訊息型別、`current_route.stop_points`
由新端點集中寫入，plan 與 upload 兩條路徑共用。

## 測試（TDD）

- `route_engine`：
  - 同區域第二次 `find_traffic_signals` 不再打 HTTP（mock 斷言呼叫次數）。
  - 不同區域（超出網格）會重打。
  - max entries 淘汰行為。
- `main.py`：
  - `plan` 回傳 `stop_points` 為空且**沒**呼叫 Overpass。
  - 新端點 `GET /api/route/traffic_signals` 回傳 stops 並寫回 `current_route`。
  - `current_route is None` → 404。

## 非目標（Non-goals）

- 不換 OSRM（不是瓶頸）。
- 不改 Overpass mirror 清單/併發策略（只有一個 mirror 有正確資料，改了也沒用）。
- 不做磁碟持久化快取（見 ② 決策紀錄）。
- 不動持久 DVT session、2Hz 注入等既有不變量。
