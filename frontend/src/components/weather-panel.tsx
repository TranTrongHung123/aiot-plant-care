"use client";

import { WeatherControl, time } from "@/lib/api";

export function WeatherPanel({
  state,
  liveStatus,
  busy,
  change,
  run,
}: {
  state: WeatherControl;
  liveStatus: { available: boolean; reason: string };
  busy: boolean;
  change: (mode: "demo" | "live", rain: boolean) => void;
  run: () => void;
}) {
  return (
    <div className="weather-panel">
      <h3>Thời tiết quyết định tưới</h3>
      <div className="segmented" role="group" aria-label="Chế độ thời tiết">
        {(["demo", "live"] as const).map((mode) => (
          <button
            key={mode}
            type="button"
            disabled={busy}
            className={state.mode === mode ? "selected" : ""}
            aria-pressed={state.mode === mode}
            onClick={() => change(mode, state.demo_rain)}
          >
            {mode.toUpperCase()}
          </button>
        ))}
      </div>
      <div className="weather-mode-stack">
        <div
          className="weather-mode-pane"
          data-active={state.mode === "demo"}
          inert={state.mode !== "demo"}
          aria-hidden={state.mode !== "demo"}
        >
          <span className="badge mock">DEMO · KỊCH BẢN TRÌNH DIỄN</span>
          <div
            className="segmented"
            role="group"
            aria-label="Kịch bản mưa DEMO"
          >
            {[true, false].map((rain) => (
              <button
                key={String(rain)}
                type="button"
                disabled={busy}
                className={state.demo_rain === rain ? "selected" : ""}
                aria-pressed={state.demo_rain === rain}
                onClick={() => change("demo", rain)}
              >
                {rain ? "Có mưa" : "Không mưa"}
              </button>
            ))}
          </div>
          <p className="small muted">
            Kịch bản chọn thủ công, không phải dự đoán XGBoost. Không cần
            Internet hoặc lịch sử thời tiết. OpenWeather vẫn thu thập nền khi có
            kết nối.
          </p>
        </div>
        <div
          className="weather-mode-pane"
          data-active={state.mode === "live"}
          inert={state.mode !== "live"}
          aria-hidden={state.mode !== "live"}
        >
          <span className="badge mock">LIVE · XGBOOST / OPENWEATHER</span>
          <div className="weather-value">
            {state.available && state.rain_probability !== null
              ? Math.round(state.rain_probability * 100)
              : "—"}
            <span>%</span>
          </div>
          <p className="small muted forecast-metadata">
            {state.forecast_start && state.forecast_end ? (
              <>
                {time(state.forecast_start)} – {time(state.forecast_end)} ·{" "}
                {state.model_version}
              </>
            ) : (
              "Chưa có dự báo LIVE còn hạn."
            )}
          </p>
          <p className="small muted forecast-readiness">{liveStatus.reason}</p>
          <button
            className="text-button"
            disabled={busy || !liveStatus.available}
            onClick={run}
          >
            Chạy dự báo mưa →
          </button>
          <p className="small muted">
            Worker tự chạy dự báo khi đủ dữ liệu. Nguồn OpenWeather đang thử
            nghiệm, chưa đánh giá sai lệch so với dữ liệu huấn luyện.
          </p>
        </div>
      </div>
      <p className="weather-decision" role="status">
        {state.reason}
      </p>
      <p className="small muted">
        Mưa chỉ chặn lần tưới tự động mới. Lệnh thủ công vẫn cần đủ điều kiện an
        toàn; dùng Dừng bơm để kết thúc lần đang chạy.
      </p>
    </div>
  );
}
