"use client";
import { usePlantcare } from "@/components/plantcare-context";
import { Icon } from "@/components/dashboard";
import { request } from "@/lib/api";
export function SimulationView() {
  const { data, apiOnline, busy, act, token } = usePlantcare();
  return (
    <section className="page-stack simulation-layout">
      <article className="card simulation-card">
        <div className="section-heading">
          <div className="panel-title">
            <span className="icon-box">
              <Icon name="bolt" />
            </span>
            <div>
              <h3>Kịch bản mô phỏng</h3>
              <p className="small muted">
                Chọn môi trường để quan sát phản hồi
              </p>
            </div>
          </div>
          <span
            className={`badge ${apiOnline && data.mqtt_connected ? "completed" : "failed"}`}
          >
            MQTT{" "}
            {!apiOnline
              ? "chưa xác định"
              : data.mqtt_connected
                ? "kết nối"
                : "ngắt"}
          </span>
        </div>
        <p className="muted small">
          Thay đổi môi trường để kiểm thử toàn bộ luồng.
        </p>
        <div className="scenarios" aria-busy={busy}>
          {[
            ["normal", "leaf", "Bình thường"],
            ["dry", "sun", "Đất khô"],
            ["rain", "cloud", "Trời mưa"],
            ["offline", "bolt", "Mất kết nối"],
          ].map(([id, icon, label]) => (
            <button
              key={id}
              aria-label={label}
              className={data.device.scenario === id ? "selected" : ""}
              aria-pressed={data.device.scenario === id}
              disabled={busy || !apiOnline}
              onClick={() =>
                act(
                  () =>
                    request("/simulation/scenario", token, {
                      method: "POST",
                      body: JSON.stringify({ scenario: id }),
                    }),
                  "Đã yêu cầu đổi kịch bản; chờ simulator xác nhận.",
                )
              }
            >
              <span className="scenario-icon">
                <Icon name={icon} size={28} />
              </span>
              <strong>{label}</strong>
              <span className="scenario-description">
                {id === "normal"
                  ? "Môi trường ổn định, theo dõi cảm biến."
                  : id === "dry"
                    ? "Độ ẩm đất thấp, kiểm tra điều kiện tưới."
                    : id === "rain"
                      ? "Mô phỏng cảm biến khi trời mưa."
                      : "Ngừng telemetry, bơm mô phỏng OFF."}
              </span>
              <span className="scenario-state">
                {data.device.scenario === id
                  ? "✓ Đang áp dụng"
                  : "Chọn kịch bản →"}
              </span>
            </button>
          ))}
        </div>
        <p className="small muted simulation-note">
          Mất kết nối: ngừng telemetry, bơm OFF. Chọn kịch bản khác để kết nối
          lại.
        </p>
      </article>
    </section>
  );
}
