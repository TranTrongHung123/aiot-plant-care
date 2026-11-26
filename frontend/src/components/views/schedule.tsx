"use client";
import { useEffect, useState } from "react";
import { usePlantcare } from "@/components/plantcare-context";
import { Icon, PolicyForm } from "@/components/dashboard";
import { WeatherPanel } from "@/components/weather-panel";
import { Command, request, time } from "@/lib/api";
import { SavedPolicy } from "@/components/saved-policy";

export function ScheduleView() {
  const { data, apiOnline, busy, act, token, runWeather, pumpOn } =
    usePlantcare();
  const latestPumpCmd: Command | undefined = data.commands.find(
    (c) => c.action === "pump_on",
  );
  const activePumpCmd =
    latestPumpCmd &&
    (latestPumpCmd.status === "requested" ||
      latestPumpCmd.status === "published" ||
      latestPumpCmd.status === "accepted")
      ? latestPumpCmd
      : undefined;

  const currentKey = latestPumpCmd
    ? `${latestPumpCmd.id}:${latestPumpCmd.status}`
    : "";
  const [initialCmdKey] = useState(() => currentKey);
  const [dismissedKey, setDismissedKey] = useState("");

  const scheduleKey = data.policy.last_schedule_at
    ? `${data.policy.last_schedule_at}:${data.policy.last_schedule_status}:${data.policy.last_schedule_reason}`
    : "";
  const [initialScheduleKey] = useState(() => scheduleKey);
  const [dismissedScheduleKey, setDismissedScheduleKey] = useState("");

  const isNewCompleted =
    Boolean(latestPumpCmd) &&
    latestPumpCmd?.status === "completed" &&
    currentKey !== initialCmdKey &&
    currentKey !== dismissedKey &&
    Date.parse(data.server_time) - Date.parse(latestPumpCmd.updated_at) <=
      15000;

  const isNewBlocked =
    Boolean(data.policy.last_schedule_at) &&
    (data.policy.last_schedule_status === "skipped" ||
      data.policy.last_schedule_status === "waiting_weather") &&
    scheduleKey !== initialScheduleKey &&
    scheduleKey !== dismissedScheduleKey &&
    Date.parse(data.server_time) -
      Date.parse(data.policy.last_schedule_at!) <=
      20000;

  useEffect(() => {
    if (!isNewCompleted || !currentKey) return;
    const timer = setTimeout(() => setDismissedKey(currentKey), 8000);
    return () => clearTimeout(timer);
  }, [isNewCompleted, currentKey]);

  useEffect(() => {
    if (!isNewBlocked || !scheduleKey) return;
    const timer = setTimeout(() => setDismissedScheduleKey(scheduleKey), 10000);
    return () => clearTimeout(timer);
  }, [isNewBlocked, scheduleKey]);

  const showLiveWatering = pumpOn || Boolean(activePumpCmd);
  const targetCmd = activePumpCmd ?? (isNewCompleted ? latestPumpCmd : undefined);
  const cmdSecs = Math.max(
    1,
    Math.round(
      (targetCmd?.duration_ms ?? data.policy.duration_seconds * 1000) / 1000,
    ),
  );
  const reasonText = data.policy.last_schedule_reason || data.policy.decision;
  const isCatchupCmd =
    Boolean(targetCmd?.weather_context?.rain_catchup) ||
    reasonText.toLowerCase().includes("tưới bù");
  const originLabel = isCatchupCmd
    ? "Tưới bù tự động"
    : targetCmd?.origin === "automatic"
      ? "Tự động theo lịch"
      : targetCmd?.origin === "manual"
        ? "Thủ công"
        : "Đang bật";

  const blockedTitle = reasonText.includes("Hoãn tưới chờ mưa")
    ? "Tạm hoãn tưới chờ mưa (Kiểm tra tưới bù sau 30s)"
    : reasonText.includes("Không tưới bù: đất đã đủ ẩm")
      ? "Hủy tưới bù (Đất đã đủ ẩm sau thời gian chờ mưa)"
      : reasonText.toLowerCase().includes("mưa")
        ? "Đã chặn tưới tự động (Dự báo có mưa)"
        : reasonText.toLowerCase().includes("độ ẩm đất đủ")
          ? "Bỏ qua tưới theo lịch (Đất đã đủ ẩm)"
          : "Đã chặn tưới theo lịch";
  const blockedIcon = reasonText.toLowerCase().includes("mưa")
    ? "cloud"
    : "clock";

  return (
    <>
      {(showLiveWatering || isNewCompleted || isNewBlocked) && (
        <div
          className={`schedule-watering-alert ${
            showLiveWatering
              ? "is-watering"
              : isNewCompleted
                ? "is-completed"
                : "is-blocked"
          }`}
          role="alert"
          aria-live="assertive"
        >
          <span className="schedule-watering-icon">
            <Icon
              name={
                showLiveWatering || isNewCompleted ? "drop" : blockedIcon
              }
              size={22}
            />
          </span>
          <div className="schedule-watering-body">
            <strong>
              {showLiveWatering
                ? `Đang tưới cây (${originLabel} · ${cmdSecs} giây)`
                : isNewCompleted
                  ? `Đã hoàn tất tưới cây (${originLabel} · ${cmdSecs} giây)`
                  : blockedTitle}
            </strong>
            <span>
              {showLiveWatering
                ? activePumpCmd?.reason ||
                  data.policy.last_schedule_reason ||
                  "Máy bơm trên ESP32 đang hoạt động."
                : isNewCompleted
                  ? `${latestPumpCmd ? time(latestPumpCmd.updated_at) : ""} · ${
                      latestPumpCmd?.reason || "ESP32 đã ngắt máy bơm an toàn."
                    }`
                  : `${
                      data.policy.last_schedule_at
                        ? `${time(data.policy.last_schedule_at)} · `
                        : ""
                    }${reasonText}`}
            </span>
          </div>
          {!showLiveWatering && (
            <button
              type="button"
              className="schedule-watering-close"
              aria-label="Đóng cảnh báo tưới"
              onClick={() => {
                if (isNewCompleted) setDismissedKey(currentKey);
                if (isNewBlocked) setDismissedScheduleKey(scheduleKey);
              }}
            >
              ×
            </button>
          )}
        </div>
      )}
      <SavedPolicy data={data} />
      <section className="two-columns schedule-layout">
        <article className="card schedule-form-card">
          <div className="section-heading">
            <div className="panel-title">
              <span className="icon-box">
                <Icon name="clock" />
              </span>
              <div>
                <h3>Quy tắc tưới tự động</h3>
                <p className="small muted">Thiết lập nhịp chăm sóc cây</p>
              </div>
            </div>
            <span className={`badge ${data.policy.enabled ? "completed" : ""}`}>
              {data.policy.enabled ? "Đang bật" : "Đang tắt"}
            </span>
          </div>
          <PolicyForm
            key={`${data.policy.enabled}-${data.policy.threshold}-${data.policy.duration_seconds}-${data.policy.cooldown_seconds}-${data.policy.schedule_kind}-${data.policy.daily_times.join(",")}-${data.policy.interval_minutes}`}
            policy={data.policy}
            busy={busy || !apiOnline}
            save={(p) =>
              act(
                () =>
                  request("/irrigation/policy", token, {
                    method: "PUT",
                    body: JSON.stringify(p),
                  }),
                "Đã lưu quy tắc tưới.",
              )
            }
          />
          <div className="policy-decision">{data.policy.decision}</div>
        </article>
        <div className="card weather-card">
          <WeatherPanel
            state={data.weather_control}
            liveStatus={data.weather_status}
            busy={busy || !apiOnline}
            run={runWeather}
            change={(mode, demo_rain) =>
              act(
                () =>
                  request("/weather/mode", token, {
                    method: "PUT",
                    body: JSON.stringify({ mode, demo_rain }),
                  }),
                "Đã đổi nguồn thời tiết cho quy tắc tưới.",
              )
            }
          />
        </div>
      </section>
    </>
  );
}
