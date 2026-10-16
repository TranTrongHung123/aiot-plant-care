"use client";
import { useState } from "react";
import { usePlantcare } from "@/components/plantcare-context";
import { statusLabel, time } from "@/lib/api";
export function HistoryView() {
  const { data } = usePlantcare();
  const [page, setPage] = useState(1);
  const pageSize = 6;
  const pageCount = Math.max(1, Math.ceil(data.commands.length / pageSize));
  const currentPage = Math.min(page, pageCount);
  const start = (currentPage - 1) * pageSize;
  const commands = data.commands.slice(start, start + pageSize);
  return (
    <section className="card history-card">
      <div className="section-heading">
        <div>
          <h3>Lịch sử tưới</h3>
          <p className="muted small">
            Lệnh điều khiển và ACK từ thiết bị mô phỏng
          </p>
        </div>
        <span className="badge">
          {data.commands.length} lệnh · tối đa 30 gần nhất
        </span>
      </div>
      <p className="table-scroll-hint">
        Vuốt ngang để xem đầy đủ trạng thái và chi tiết →
      </p>
      <div
        className="table-wrap"
        tabIndex={0}
        role="region"
        aria-label="Bảng lịch sử tưới"
      >
        <table>
          <thead>
            <tr>
              <th>Thời gian</th>
              <th>Thao tác</th>
              <th>Nguồn</th>
              <th>Trạng thái</th>
              <th>Chi tiết</th>
            </tr>
          </thead>
          <tbody>
            {commands.map((c) => (
              <tr key={c.id}>
                <td>
                  <strong>{time(c.issued_at)}</strong>
                  <span className="table-date">
                    {new Date(c.issued_at).toLocaleDateString("vi-VN", {
                      timeZone: "Asia/Ho_Chi_Minh",
                    })}
                  </span>
                </td>
                <td>
                  {c.action === "pump_on"
                    ? `Tưới ${c.duration_ms / 1000}s`
                    : "Dừng bơm"}
                </td>
                <td>
                  <span className="badge">
                    {c.origin === "automatic" ? "Tự động" : "Thủ công"}
                  </span>
                </td>
                <td>
                  <span className={`badge ${c.status}`}>
                    {statusLabel[c.status] ?? c.status}
                  </span>
                </td>
                <td>
                  <details>
                    <summary>Xem chi tiết</summary>
                    <p className="small">
                      {c.reason || "Chưa có lý do bổ sung"}
                    </p>
                    {c.events.map((e, i) => (
                      <p className="small" key={i}>
                        {time(e.at)} · {statusLabel[e.status]} · {e.reason}
                      </p>
                    ))}
                  </details>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!data.commands.length && (
          <div className="empty">
            Chưa có lần tưới nào. Lệnh và ACK sẽ được lưu tại đây.
          </div>
        )}
      </div>
      <div className="pagination-bar">
        <p className="muted small" role="status">
          {data.commands.length
            ? `${start + 1}–${Math.min(start + pageSize, data.commands.length)} trong ${data.commands.length} lệnh`
            : "Chưa có lệnh"}
        </p>
        <nav className="pagination" aria-label="Phân trang lịch sử tưới">
          <button
            aria-label="Trang trước"
            disabled={currentPage === 1}
            onClick={() => setPage(currentPage - 1)}
          >
            ←
          </button>
          {Array.from({ length: pageCount }, (_, index) => index + 1).map(
            (number) => (
              <button
                key={number}
                aria-label={`Trang ${number}`}
                aria-current={number === currentPage ? "page" : undefined}
                onClick={() => setPage(number)}
              >
                {number}
              </button>
            ),
          )}
          <button
            aria-label="Trang sau"
            disabled={currentPage === pageCount}
            onClick={() => setPage(currentPage + 1)}
          >
            →
          </button>
        </nav>
      </div>
    </section>
  );
}
