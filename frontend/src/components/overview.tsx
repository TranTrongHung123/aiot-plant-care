import Link from "next/link";
import { Dashboard, time } from "@/lib/api";
import { nextSchedule } from "@/lib/overview";
import { Icon } from "./dashboard";

function SensorSparkline({
  values,
  label,
}: {
  values: (number | null | undefined)[];
  label: string;
}) {
  const valid = values.filter(
    (value): value is number => value != null && Number.isFinite(value),
  );
  if (valid.length < 2)
    return <span className="sparkline-empty">Chưa đủ lịch sử</span>;
  const low = Math.min(...valid);
  const range = Math.max(...valid) - low;
  const segments: string[] = [];
  let segment = "";
  values.forEach((value, index) => {
    if (value == null || !Number.isFinite(value)) {
      if (segment) segments.push(segment);
      segment = "";
      return;
    }
    segment += `${segment ? " L" : "M"}${(index / Math.max(1, values.length - 1)) * 160},${range ? 34 - ((value - low) / range) * 26 : 21}`;
  });
  if (segment) segments.push(segment);
  return (
    <svg
      className="sensor-sparkline"
      viewBox="0 0 160 42"
      role="img"
      aria-label={`${label}: xu hướng tương đối của ${values.length} bản ghi gần nhất`}
    >
      <title>Xu hướng tương đối; không dùng để đọc giá trị tuyệt đối</title>
      {segments.map((path, index) => (
        <path
          key={index}
          d={path}
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      ))}
    </svg>
  );
}

function SoilGauge({
  value,
  stale,
}: {
  value: number | null | undefined;
  stale: boolean;
}) {
  return (
    <div className={`soil-gauge ${stale ? "is-stale" : ""}`}>
      <svg viewBox="0 0 160 160" aria-hidden="true">
        <circle className="gauge-track" cx="80" cy="80" r="67" />
        <circle
          className="gauge-fill"
          cx="80"
          cy="80"
          r="67"
          pathLength="100"
          strokeDasharray={`${value == null ? 0 : Math.max(0, Math.min(100, value))} 100`}
          transform="rotate(-90 80 80)"
        />
      </svg>
      <div>
        <Icon name="drop" size={19} />
        <strong>
          {value ?? "—"}
          {value != null && <small>%</small>}
        </strong>
        <span>{stale ? "Dữ liệu cũ" : "Độ ẩm đất"}</span>
      </div>
    </div>
  );
}

function SummaryRow({
  label,
  value,
  warning = false,
}: {
  label: string;
  value: string;
  warning?: boolean;
}) {
  return (
    <div className="summary-row">
      <span>{label}</span>
      <strong
        className={warning || value.startsWith("Chưa") ? "warning-text" : ""}
      >
        {value}
      </strong>
    </div>
  );
}

export function Overview({
  data,
  apiOnline,
}: {
  data: Dashboard;
  apiOnline: boolean;
}) {
  const stale = data.stale || !apiOnline;
  const readings = data.latest?.readings;
  const soil = readings?.soil_moisture_pct;
  const dry = soil != null && soil < data.policy.threshold;
  const next = nextSchedule(data.policy, data.server_time);
  const leaf = data.jobs.find(
    (job) => job.task === "leaf" && job.status === "completed" && job.result,
  );
  const alerts = [
    ...(!apiOnline
      ? [
          {
            title: "Mất kết nối backend",
            text: "Đang hiển thị dữ liệu nhận được lần cuối.",
            href: "/control",
          },
        ]
      : []),
    ...(stale
      ? [
          {
            title: "Cảm biến chưa có dữ liệu mới",
            text: "Kiểm tra kết nối trước khi đánh giá tình trạng đất.",
            href: "/simulation",
          },
        ]
      : []),
    ...(!data.device.online
      ? [
          {
            title: "Thiết bị ngoại tuyến",
            text: "Thiết bị chưa sẵn sàng nhận lệnh tưới.",
            href: "/control",
          },
        ]
      : []),
    ...(!data.mqtt_connected
      ? [
          {
            title: "MQTT mất kết nối",
            text: "Chưa thể trao đổi lệnh với thiết bị.",
            href: "/control",
          },
        ]
      : []),
    ...(soil == null
      ? [
          {
            title: "Chưa có độ ẩm đất hợp lệ",
            text: "Không thể đánh giá ngưỡng tưới từ dữ liệu hiện có.",
            href: "/control",
          },
        ]
      : !stale && dry
        ? [
            {
              title: "Độ ẩm đất dưới ngưỡng tưới",
              text: `Hiện tại ${soil}% · Ngưỡng đã đặt ${data.policy.threshold}%.`,
              href: "/control",
            },
          ]
        : []),
    ...(data.policy.enabled && !data.weather_control.available
      ? [
          {
            title: "Thời tiết chưa sẵn sàng",
            text: data.weather_control.reason,
            href: "/schedule",
          },
        ]
      : []),
    ...(data.jobs.some((job) => job.status === "failed")
      ? [
          {
            title: "Có tác vụ AI thất bại",
            text: "Xem nguyên nhân và kết quả trong lịch sử phân tích.",
            href: "/ai",
          },
        ]
      : []),
  ];
  const metrics = [
    {
      label: "Độ ẩm đất",
      history: data.history.map((item) => item.readings.soil_moisture_pct),
      value: soil,
      unit: "%",
      icon: "drop",
      note:
        soil == null
          ? "Chưa có giá trị hợp lệ"
          : `Ngưỡng tưới ${data.policy.threshold}%`,
    },
    {
      label: "Nhiệt độ",
      history: data.history.map((item) => item.readings.temperature_c),
      value: readings?.temperature_c,
      unit: "°C",
      icon: "sun",
      note: "Môi trường tại cây",
    },
    {
      label: "Độ ẩm không khí",
      history: data.history.map((item) => item.readings.humidity_pct),
      value: readings?.humidity_pct,
      unit: "%",
      icon: "cloud",
      note: "Môi trường tại cây",
    },
    {
      label: "Áp suất",
      history: data.history.map((item) => item.readings.pressure_hpa),
      value: readings?.pressure_hpa,
      unit: "hPa",
      icon: "bolt",
      note: "Áp suất khí quyển",
    },
  ];
  return (
    <div className="overview-content">
      <section
        className="overview-top"
        aria-label="Tình trạng cây và lịch tưới"
      >
        <article className="plant-summary">
          <div className="section-heading">
            <span className="plant-label">
              <Icon name="leaf" />
              Cà chua
            </span>
            <span className="badge">Theo cảm biến</span>
          </div>
          <div className="plant-summary-main">
            <div>
              <h2>
                {stale || soil == null
                  ? "Cần kiểm tra dữ liệu"
                  : dry
                    ? "Đất đang cần thêm ẩm"
                    : "Độ ẩm đất trên ngưỡng"}
              </h2>
              <p>
                {stale || soil == null
                  ? "Chưa đủ dữ liệu mới để đánh giá nhu cầu tưới."
                  : `Độ ẩm đất ${soil}%, ${dry ? "thấp hơn" : "đạt hoặc cao hơn"} ngưỡng tưới ${data.policy.threshold}% đã cấu hình.`}
              </p>
            </div>
            <SoilGauge value={soil} stale={stale} />
          </div>
          <div className="plant-summary-footer">
            <div className="plant-health">
              <span>Kết quả lá gần nhất</span>
              <strong>
                {leaf?.result?.label ?? "Chưa có kết quả phân tích"}
                {leaf?.result?.is_mock && (
                  <span className="badge mock">Mock cũ</span>
                )}
              </strong>
              <details>
                <summary>Thông tin kết quả</summary>
                <p>
                  {leaf
                    ? `${leaf.result?.is_mock ? "Mock cũ · " : ""}${new Date(leaf.created_at).toLocaleString("vi-VN", { timeZone: "Asia/Ho_Chi_Minh" })} · Nguồn: ${leaf.source}. Kết quả ảnh gần nhất, không phải đánh giá hiện tại.`
                    : "Cần ảnh lá để đánh giá bằng AI"}
                </p>
              </details>
            </div>
            <Link href="/ai" className="text-button">
              Xem phân tích cây <span aria-hidden="true">↗</span>
            </Link>
          </div>
        </article>
        <article className="card next-watering">
          <div className="section-heading">
            <h3>Lịch tưới tiếp theo</h3>
            <span className="schedule-icon">
              <Icon name="clock" />
            </span>
          </div>
          <span className="badge">
            {data.policy.enabled ? "Tự động theo lịch" : "Chế độ thủ công"}
          </span>
          <div className="schedule-time">
            {next
              ? new Date(next).toLocaleTimeString("vi-VN", {
                  timeZone: "Asia/Ho_Chi_Minh",
                  hour: "2-digit",
                  minute: "2-digit",
                })
              : data.policy.enabled
                ? "Chưa xác định"
                : "Chưa bật lịch"}
          </div>
          <p className="muted">
            {next
              ? new Date(next).toLocaleDateString("vi-VN", {
                  timeZone: "Asia/Ho_Chi_Minh",
                  day: "2-digit",
                  month: "2-digit",
                  year: "numeric",
                }) + " · Giờ Việt Nam"
              : "Bật lịch để chăm cây theo nhịp phù hợp."}
          </p>
          <p className="schedule-note">
            {data.policy.enabled
              ? "Mốc dự kiến. Chỉ tưới khi đất, thời tiết và thiết bị đủ điều kiện."
              : "Bạn vẫn có thể gửi lệnh tại trang Điều khiển tưới."}
          </p>
          <Link href="/schedule" className="text-button">
            Quản lý lịch tưới <span aria-hidden="true">→</span>
          </Link>
        </article>
      </section>
      <section aria-labelledby="sensor-title">
        <div className="section-heading">
          <h2 id="sensor-title" className="section-title">
            Môi trường tại cây
          </h2>
          <span className="muted small">
            {stale ? "Dữ liệu cũ" : "Nguồn: simulator"}
            {data.latest && (
              <span className="sensor-timestamp">
                Đo lúc {time(data.latest.measured_at)} · Giờ Việt Nam
              </span>
            )}
          </span>
        </div>
        <div className="metrics">
          {metrics.map((metric) => (
            <article
              key={metric.label}
              className={`card metric ${stale ? "stale" : ""}`}
            >
              <div className="metric-top">
                <span>{metric.label}</span>
                <Icon name={metric.icon} size={20} />
              </div>
              <div className="metric-value">
                {metric.value ?? "—"}
                <span>{metric.unit}</span>
              </div>
              <div className="metric-trend">
                <SensorSparkline values={metric.history} label={metric.label} />
              </div>
              <p className="small muted">
                {stale ? "Chưa có dữ liệu mới" : metric.note}
              </p>
            </article>
          ))}
        </div>
      </section>
      <section className="two-columns overview-bottom">
        <article className="card alerts">
          <div className="section-heading">
            <h2 className="section-title">Cần chú ý</h2>
            <span className="badge">{alerts.length} thông báo</span>
          </div>
          {alerts.length ? (
            alerts.map((alert) => (
              <Link className="alert-row" href={alert.href} key={alert.title}>
                <span className="alert-mark" aria-hidden="true">
                  !
                </span>
                <div>
                  <h3>{alert.title}</h3>
                  <p>{alert.text}</p>
                </div>
                <span aria-hidden="true">↗</span>
              </Link>
            ))
          ) : (
            <div className="clear-state">
              <span className="clear-check" aria-hidden="true">
                ✓
              </span>
              <h3>Chưa có cảnh báo vận hành</h3>
              <p>
                Cảm biến và kết nối hiện không có cảnh báo. Đây không phải kết
                luận cây không mắc bệnh.
              </p>
            </div>
          )}
        </article>
        <article className="card device-summary">
          <div className="section-heading">
            <h2 className="section-title">Trạng thái thiết bị</h2>
            <Icon name="bolt" size={20} />
          </div>
          <p className="device-name">{data.device.name}</p>
          <SummaryRow
            label="Thiết bị"
            value={
              !apiOnline
                ? "Chưa xác định"
                : data.device.online
                  ? "Đang kết nối"
                  : "Ngoại tuyến"
            }
            warning={!apiOnline || !data.device.online}
          />
          <SummaryRow
            label="MQTT"
            value={
              !apiOnline
                ? "Chưa xác định"
                : data.mqtt_connected
                  ? "Đã kết nối"
                  : "Mất kết nối"
            }
            warning={!apiOnline || !data.mqtt_connected}
          />
          <SummaryRow
            label="Relay bơm"
            value={
              !apiOnline || data.device.pump === "unknown"
                ? "Chưa rõ trạng thái"
                : data.device.pump === "on"
                  ? "Đang bật (ON)"
                  : "Đang tắt (OFF)"
            }
          />
          <p className="small muted device-note">
            Trạng thái relay không xác nhận có nước chảy.
          </p>
          <Link href="/control" className="text-button">
            Đến điều khiển tưới <span aria-hidden="true">→</span>
          </Link>
        </article>
      </section>
    </div>
  );
}
