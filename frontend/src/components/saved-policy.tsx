import { Dashboard, time } from "@/lib/api";
import { nextSchedule } from "@/lib/overview";
import { Icon } from "./dashboard";

export function SavedPolicy({ data }: { data: Dashboard }) {
  const policy = data.policy;
  const next = nextSchedule(policy, data.server_time);
  return (
    <section className="card saved-policy" aria-labelledby="saved-policy-title">
      <div className="section-heading">
        <div className="panel-title">
          <span className="icon-box">
            <Icon name="clock" />
          </span>
          <div>
            <h2 id="saved-policy-title" className="section-title">
              Quy tắc đã lưu
            </h2>
            <p className="small muted">Cấu hình đang được backend áp dụng</p>
          </div>
        </div>
        <span className={`badge ${policy.enabled ? "completed" : ""}`}>
          {policy.enabled ? "Tự động đang bật" : "Tự động đang tắt"}
        </span>
      </div>
      <div className="saved-policy-grid">
        <div className="saved-schedule">
          <span className="muted small">
            {policy.schedule_kind === "daily"
              ? "Giờ tưới hằng ngày"
              : "Chu kỳ lặp"}
          </span>
          <div className="saved-time-list">
            {policy.schedule_kind === "daily" ? (
              policy.daily_times.map((value) => (
                <strong key={value}>{value}</strong>
              ))
            ) : (
              <strong>Mỗi {policy.interval_minutes} phút</strong>
            )}
          </div>
          <p className="small muted">
            Giờ Việt Nam ·{" "}
            {policy.enabled
              ? next
                ? `Mốc dự kiến tiếp theo: ${new Date(next).toLocaleString("vi-VN", { timeZone: "Asia/Ho_Chi_Minh" })}`
                : "Chưa xác định được mốc tiếp theo."
              : "Lịch được lưu, chưa chạy khi tự động tắt."}
          </p>
        </div>
        <dl className="saved-conditions">
          <div>
            <dt>Ngưỡng đất</dt>
            <dd>{policy.threshold}%</dd>
          </div>
          <div>
            <dt>Thời lượng</dt>
            <dd>{policy.duration_seconds} giây</dd>
          </div>
          <div>
            <dt>Nghỉ giữa lần tưới</dt>
            <dd>{policy.cooldown_seconds} giây</dd>
          </div>
          <div>
            <dt>Nguồn thời tiết</dt>
            <dd>{data.weather_control.mode.toUpperCase()}</dd>
          </div>
        </dl>
      </div>
      <p className="small muted">
        Đến lịch vẫn kiểm tra đất, thời tiết, kết nối và điều kiện an toàn.{" "}
        {policy.last_schedule_at &&
          `Lượt gần nhất ${time(policy.last_schedule_at)}: ${policy.last_schedule_reason}`}
      </p>
    </section>
  );
}
