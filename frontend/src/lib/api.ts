export const API =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";
export const WS = process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8000/ws";
export type Reading = {
  id: string;
  measured_at: string;
  readings: {
    temperature_c: number;
    humidity_pct: number;
    soil_moisture_pct: number | null;
    pressure_hpa: number;
  };
};
export type Job = {
  source: string;
  provider: string;
  model_version: string;
  id: string;
  task: string;
  image_id: string | null;
  status: string;
  created_at: string;
  error: string | null;
  result: null | {
    label: string;
    is_mock: boolean;
    target_model: string;
    confidence?: number;
    rain_probability?: number;
    count?: number;
    notice: string;
    forecast_start?: string;
    forecast_end?: string;
    detections?: { label: string; confidence: number; box: number[] }[];
  };
};
export type Command = {
  id: string;
  action: string;
  duration_ms: number;
  status: string;
  reason: string;
  origin: string;
  issued_at: string;
  events: { status: string; reason: string; at: string }[];
};
export type Policy = {
  enabled: boolean;
  threshold: number;
  duration_seconds: number;
  cooldown_seconds: number;
  decision: string;
  schedule_kind: "daily" | "interval";
  daily_times: string[];
  interval_minutes: number;
  schedule_started_at: string;
  last_schedule_at: string | null;
  last_schedule_reason: string;
  last_schedule_status: string;
};
export type WeatherControl = {
  mode: "demo" | "live";
  demo_rain: boolean;
  available: boolean;
  rain_expected: boolean | null;
  rain_probability: number | null;
  reason: string;
  forecast_start: string | null;
  forecast_end: string | null;
  model_version: string | null;
};
export type Dashboard = {
  weather_control: WeatherControl;
  weather_status: { available: boolean; reason: string };
  server_time: string;
  mqtt_connected: boolean;
  stale: boolean;
  device: { name: string; online: boolean; pump: string; scenario: string };
  latest: Reading | null;
  history: Reading[];
  jobs: Job[];
  commands: Command[];
  policy: Policy;
};

export async function request<T>(
  path: string,
  token: string,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !(init.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  const response = await fetch(`${API}${path}`, {
    ...init,
    headers,
    signal: init.signal ?? AbortSignal.timeout(15000),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(
      typeof error.detail === "string"
        ? error.detail
        : `Yêu cầu thất bại (${response.status})`,
    );
  }
  return response.json();
}
export const time = (value: string) =>
  new Date(value).toLocaleTimeString("vi-VN", {
    timeZone: "Asia/Ho_Chi_Minh",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
export const statusLabel: Record<string, string> = {
  queued: "Đang chờ",
  running: "Đang xử lý",
  completed: "Hoàn tất",
  failed: "Thất bại",
  published: "Đã gửi",
  requested: "Yêu cầu",
  accepted: "Đã nhận ACK",
  rejected: "Từ chối",
  timed_out: "Hết thời gian chờ",
};
