"use client";
import { usePlantcare } from "@/components/plantcare-context";
import { Icon, Trend } from "@/components/dashboard";
export function ControlView() {
  const { data, apiOnline, busy, stale, pumpOn, duration, setDuration, pump } =
    usePlantcare();
  return (
    <section className="two-columns">
      <article className="card irrigation">
        <div className="section-heading">
          <h3>Điều khiển tưới</h3>
          <span className={`badge ${pumpOn ? "running" : ""}`}>
            {!apiOnline || data.device.pump === "unknown"
              ? "Chưa rõ trạng thái"
              : pumpOn
                ? "Relay ON"
                : "Relay OFF"}
          </span>
        </div>
        <div className={`pump-symbol ${pumpOn ? "watering" : ""}`}>
          <Icon name="drop" size={34} />
        </div>
        <p className="muted small center">Bơm mô phỏng · tối đa 15 giây/lần</p>
        <label className="duration-label">
          Thời lượng
          <input
            aria-label="Thời lượng tưới"
            type="number"
            min={1}
            max={15}
            value={duration}
            onChange={(e) => setDuration(Number(e.target.value))}
          />
          giây
        </label>
        <div className="button-row">
          <button
            className="button"
            disabled={
              busy ||
              stale ||
              !data.device.online ||
              pumpOn ||
              !data.mqtt_connected ||
              duration < 1 ||
              duration > 15
            }
            onClick={() => pump("pump_on")}
          >
            Tưới ngay
          </button>
          <button
            className="button stop"
            disabled={busy || !apiOnline || !data.mqtt_connected}
            onClick={() => pump("pump_off")}
          >
            Dừng bơm
          </button>
        </div>
        <p className="small muted center">
          ACK relay không xác nhận lưu lượng nước.
        </p>
      </article>
      <article className="card">
        <h3>Diễn biến độ ẩm đất</h3>
        <p className="muted">
          60 bản ghi gần nhất · Ngưỡng tưới {data.policy.threshold}%
        </p>
        <Trend data={data} />
      </article>
    </section>
  );
}
