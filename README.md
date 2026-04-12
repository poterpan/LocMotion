# LocMotion

**macOS 上的 iOS GPS 模擬工具**，附帶 Web 控制台。產生擬真的 GPS 軌跡——不是死板地設定一個固定點，而是以 2Hz 連續注入座標，模擬加減速、紅綠燈停頓、GPS 抖動等細節，讓模擬位置難以和真實 GPS 分辨。

適用情境：
- 測試依賴 GPS 的 iOS App
- 路線模擬（例如在家附近繞路線測試導航 App）
- 模擬移動到特定地點

## 主要功能

- **路線規劃** — 用 OSRM 規劃駕駛/步行/騎車路線，支援多個中途點
- **地圖互動** — 單擊加路線點、雙擊設靜態定位、拖曳調整點位
- **擬真模擬** — 加減速曲線、速度變異、紅綠燈自動停頓、GPS 抖動、靜止漂移
- **進階選項** — 返回起點、循環繞行、從目前位置出發（接續導航）
- **地址搜尋** — 地標/地址 autocomplete，整合 OpenStreetMap Nominatim
- **檔案匯入** — GPX / GeoJSON 軌跡檔案
- **裝置支援** — USB + WiFi，iOS <17 和 17+ 都支援

## 前置需求

- **macOS**（不支援 Windows/Linux）
- **Python 3.10+**
- **iOS 裝置**，已啟用 Developer Mode
- **sudo 權限**（pymobiledevice3 需要）

## 安裝

```bash
git clone <repo-url>
cd LocMotion

# 建立虛擬環境（推薦用 uv，也可以用 venv）
uv venv .venv
source .venv/bin/activate
uv pip install -r requirements.txt
```

## 執行

```bash
sudo .venv/bin/python -m src.main
```

瀏覽器打開 `http://localhost:8080`。

## 使用方式

### 基本流程

1. 連接 iOS 裝置（USB 或 WiFi）
2. 右上角選擇裝置 → 按「連線」
3. 在地圖上**單擊**加入路線點（S=起點，數字=中途點，E=終點）
4. 按「規劃路線」→ 地圖顯示藍色路線 + 紅色紅綠燈點
5. 按「開始」→ 裝置位置開始沿路線移動

### 進階操作

- **接續導航（A→B→C）**：到達 B 後，點擊 C → 勾選「從目前位置出發」→ 規劃路線 → 開始
- **模擬中改道**：模擬中勾選「從目前位置出發」→ 點新目的地 → 規劃路線 → 開始（會自動切換到新路線）
- **持續繞圈**：勾選「循環」，到達終點後會自動從頭開始
- **返回起點**：勾選「返回起點」，終點後會自動導航回起點
- **靜態定位**：在地圖上**雙擊**任一位置，裝置會瞬移到該點並停留（帶擬真漂移）

### 按鈕行為對照

| 當前狀態 | 開始按鈕 |
|---------|---------|
| 閒置 / 已停止 / 已完成 | 從路線起點開始 |
| 暫停中（路線未改） | 從暫停處繼續 |
| 暫停中（路線已改） | 用新路線從頭開始 |
| 移動中（路線已改） | 切換到新路線 |

**停止** = 清除模擬位置，裝置回到真實 GPS。  
**暫停** = 停止移動但保留位置。

## 架構概覽

```
Frontend (Leaflet + Tailwind + Vanilla JS)
          │
          │ HTTP + WebSocket
          ▼
FastAPI Backend
  ├── DeviceManager      iOS 裝置連線（pymobiledevice3）
  ├── RouteEngine        OSRM + Overpass + Nominatim
  ├── MotionEngine       模擬迴圈（加減速、停頓、2Hz tick）
  └── GPSNoiseEngine     GPS 抖動與靜止漂移
          │
          ▼ DVT LocationSimulation
      iOS Device
```

詳細設計見 `docs/specs/2026-04-12-locmotion-design.md`。

## 限制

- **速度顯示**：`LocationSimulation` 協議不支援 speed 屬性。依賴 `CLLocation.speed` 的測速 App（如 Speedometer）會顯示 0；但從位置差分計算速度的 App（如 Google Maps 導航）可以正常顯示。
- **外部 API 依賴**：OSRM、Overpass、Nominatim 均為公開服務，偶爾會慢或失敗。系統已有 retry + mirror fallback，但路線規劃偶爾會失敗，重試即可。
- **紅綠燈準確性**：資料來自 OpenStreetMap，在某些地區可能不完整。找不到紅綠燈時會靜默略過。

## 鳴謝

- **[GeoPort](https://github.com/davesc63/GeoPort)**：裝置連線邏輯的原型來源
- **[pymobiledevice3](https://github.com/doronz88/pymobiledevice3)**：核心 iOS 通訊函式庫
- **OpenStreetMap**：路線規劃、地址搜尋、紅綠燈資料

## License

MIT
