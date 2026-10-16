"use client";
import { useState } from "react";
import { Job, statusLabel, time } from "@/lib/api";
import { Icon } from "./dashboard";

const taskNames: Record<string, string> = {
  weather: "Dự báo mưa",
  leaf: "Bệnh lá cà chua",
  fruit: "Độ chín quả",
};
export function AiHistory({
  jobs,
  selectedId,
  select,
}: {
  jobs: Job[];
  selectedId?: string;
  select: (id: string) => void;
}) {
  const [filter, setFilter] = useState("all");
  const [page, setPage] = useState(1);
  const sorted = [...jobs]
    .filter((job) => filter === "all" || job.task === filter)
    .sort(
      (a, b) =>
        Date.parse(b.created_at) - Date.parse(a.created_at) ||
        b.id.localeCompare(a.id),
    );
  const pageCount = Math.max(1, Math.ceil(sorted.length / 6));
  const current = Math.min(page, pageCount);
  const visible = sorted.slice((current - 1) * 6, current * 6);
  const dateLabel = (date: string) =>
    new Date(date).toLocaleDateString("vi-VN", {
      timeZone: "Asia/Ho_Chi_Minh",
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
    });
  return (
    <section className="card ai-history" aria-labelledby="ai-history-title">
      <div className="section-heading">
        <div>
          <h2 id="ai-history-title" className="section-title">
            Lịch sử tác vụ AI
          </h2>
          <p className="small muted">
            Mới nhất trước · Nhóm theo ngày Việt Nam · Tối đa 30 tác vụ
          </p>
        </div>
        <label className="history-filter">
          Loại phân tích
          <select
            value={filter}
            onChange={(event) => {
              setFilter(event.target.value);
              setPage(1);
            }}
          >
            <option value="all">Tất cả</option>
            <option value="leaf">Bệnh lá</option>
            <option value="fruit">Độ chín quả</option>
            <option value="weather">Dự báo mưa</option>
          </select>
        </label>
      </div>
      <div className="ai-timeline">
        {visible.map((job, index) => (
          <div key={job.id}>
            {(index === 0 ||
              dateLabel(visible[index - 1].created_at) !==
                dateLabel(job.created_at)) && (
              <h3 className="timeline-day">{dateLabel(job.created_at)}</h3>
            )}
            <article
              className={`timeline-entry ${job.id === selectedId ? "selected" : ""}`}
              data-created-at={job.created_at}
            >
              <time dateTime={job.created_at}>{time(job.created_at)}</time>
              <span className="icon-box">
                <Icon
                  name={
                    job.task === "weather"
                      ? "cloud"
                      : job.task === "leaf"
                        ? "leaf"
                        : "image"
                  }
                />
              </span>
              <div className="timeline-content">
                <h3>{taskNames[job.task] ?? job.task}</h3>
                <p>
                  {job.result?.label ?? statusLabel[job.status] ?? job.status}
                </p>
                <p className="small muted">
                  {job.provider === "mock" ? "Mock cũ · " : ""}Nguồn:{" "}
                  {job.source}
                </p>
                {job.result?.forecast_start && job.result?.forecast_end && (
                  <p className="forecast-range">
                    Khoảng dự báo: {dateLabel(job.result.forecast_start)} ·{" "}
                    {time(job.result.forecast_start)}–
                    {time(job.result.forecast_end)}
                  </p>
                )}
                <details>
                  <summary>Thông tin tác vụ</summary>
                  <p className="small muted">Model: {job.model_version}</p>
                  <p className="small">
                    {job.error ??
                      job.result?.notice ??
                      "Đang chờ kết quả xử lý."}
                  </p>
                </details>
              </div>
              <div className="timeline-actions">
                <span className={`badge ${job.status}`}>
                  {statusLabel[job.status] ?? job.status}
                </span>
                {job.image_id && (
                  <button
                    className="text-button"
                    onClick={() => select(job.id)}
                    aria-pressed={job.id === selectedId}
                  >
                    Xem kết quả ↗
                  </button>
                )}
              </div>
            </article>
          </div>
        ))}
        {!visible.length && (
          <p className="empty">Chưa có tác vụ thuộc loại này.</p>
        )}
      </div>
      <div className="pagination-bar">
        <span className="small muted">
          {sorted.length
            ? `${(current - 1) * 6 + 1}–${Math.min(current * 6, sorted.length)} trong ${sorted.length} tác vụ`
            : "0 tác vụ"}
        </span>
        <nav className="pagination" aria-label="Phân trang tác vụ AI">
          <button
            aria-label="Trang AI trước"
            disabled={current === 1}
            onClick={() => setPage(current - 1)}
          >
            ←
          </button>
          {Array.from({ length: pageCount }, (_, i) => i + 1).map((number) => (
            <button
              key={number}
              aria-current={number === current ? "page" : undefined}
              aria-label={`Trang AI ${number}`}
              onClick={() => setPage(number)}
            >
              {number}
            </button>
          ))}
          <button
            aria-label="Trang AI sau"
            disabled={current === pageCount}
            onClick={() => setPage(current + 1)}
          >
            →
          </button>
        </nav>
      </div>
    </section>
  );
}
