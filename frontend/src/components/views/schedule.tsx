"use client";
import { usePlantcare } from "@/components/plantcare-context";
import { Icon, PolicyForm } from "@/components/dashboard";
import { WeatherPanel } from "@/components/weather-panel";
import { request } from "@/lib/api";
import { SavedPolicy } from "@/components/saved-policy";
export function ScheduleView() {
  const { data, apiOnline, busy, act, token, runWeather } = usePlantcare();
  return (
    <>
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
