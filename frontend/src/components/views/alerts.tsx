"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { request } from "@/lib/api";
import { usePlantcare } from "../plantcare-context";
import styles from "./alerts.module.css";

type AlertState = "attention" | "unread" | "all";
type Alert = {
  id: string;
  kind:
    | "device_offline"
    | "sensor_stale"
    | "low_soil"
    | "irrigation_error"
    | "leaf_review";
  trigger_type: "condition" | "event";
  severity: "warning" | "info";
  source_kind: "device" | "reading" | "command" | "image";
  source_ref: string | null;
  data_source: string;
  observed_at: string;
  created_at: string;
  acknowledged_at: string | null;
  resolved_at: string | null;
  evidence: { model_version?: string | null; job_id?: string | null };
  title: string;
  description: string;
};
type AlertPage = {
  items: Alert[];
  unread_count: number;
  next_cursor: string | null;
};

const dateTime = new Intl.DateTimeFormat("vi-VN", {
  timeZone: "Asia/Ho_Chi_Minh",
  dateStyle: "short",
  timeStyle: "short",
});

function useAlerts(
  token: string,
  state: AlertState,
  cursor: string | null,
  limit: number,
) {
  const query = new URLSearchParams({ state, limit: String(limit) });
  if (cursor) query.set("cursor", cursor);
  const path = `/alerts?${query}`;
  const [snapshot, setSnapshot] = useState<{
    path: string;
    page: AlertPage;
  } | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const inflight = useRef<AbortController | null>(null);
  const refresh = useCallback(async () => {
    inflight.current?.abort();
    const controller = new AbortController();
    inflight.current = controller;
    try {
      const page = await request<AlertPage>(path, token, {
        signal: AbortSignal.any([controller.signal, AbortSignal.timeout(9000)]),
      });
      if (!controller.signal.aborted) {
        setSnapshot({ path, page });
        setFailed(null);
      }
    } catch {
      if (!controller.signal.aborted) setFailed(path);
    }
  }, [path, token]);

  useEffect(() => {
    const initial = window.setTimeout(refresh, 0);
    const timer = window.setInterval(refresh, 10000);
    return () => {
      window.clearTimeout(initial);
      window.clearInterval(timer);
      inflight.current?.abort();
    };
  }, [refresh]);

  return {
    page: snapshot?.path === path ? snapshot.page : null,
    failed: failed === path,
    refresh,
  };
}

function AlertStatus({
  failed,
  page,
}: {
  failed: boolean;
  page: AlertPage | null;
}) {
  if (failed) {
    return (
      <p className="warning-text" role="status">
        {page
          ? "Đang hiển thị danh sách cảnh báo nhận được lần cuối."
          : "Chưa tải được cảnh báo."}
      </p>
    );
  }
  if (!page)
    return (
      <p className="muted" role="status">
        Đang tải cảnh báo…
      </p>
    );
  if (!page.items.length)
    return <p className="muted">Chưa có cảnh báo trong danh sách này.</p>;
  return null;
}

export function OverviewAlerts() {
  const { token, apiOnline } = usePlantcare();
  const { page, failed } = useAlerts(token, "attention", null, 4);
  return (
    <article className="card alerts">
      <div className="section-heading">
        <h2 className="section-title">Cần chú ý</h2>
        {page && <span className="badge">{page.unread_count} chưa xem</span>}
      </div>
      <AlertStatus failed={failed || !apiOnline} page={page} />
      {page?.items.map((alert) => (
        <Link className="alert-row" href="/alerts" key={alert.id}>
          <span className="alert-mark" aria-hidden="true">
            !
          </span>
          <div>
            <h3>{alert.title}</h3>
            <p>{alert.description}</p>
          </div>
          <span aria-hidden="true">↗</span>
        </Link>
      ))}
      <Link href="/alerts" className="text-button">
        Xem lịch sử cảnh báo
      </Link>
    </article>
  );
}

const sources = {
  device: { title: "Thiết bị", href: "/control" },
  reading: { title: "Số đo cảm biến", href: "/" },
  command: { title: "Lệnh tưới", href: "/history" },
  image: { title: "Phân tích ảnh", href: "/ai" },
};

function AlertItem({
  alert,
  pending,
  acknowledge,
}: {
  alert: Alert;
  pending: boolean;
  acknowledge: (id: string) => void;
}) {
  const source = sources[alert.source_kind];
  return (
    <li className={styles.item}>
      <div>
        <div className={styles.labels}>
          <span
            className={alert.severity === "warning" ? "badge warning" : "badge"}
          >
            {alert.severity === "warning" ? "Cảnh báo" : "Cần xem lại"}
          </span>
          {alert.acknowledged_at && <span className="badge">Đã xem</span>}
          {alert.resolved_at ? (
            <span className="badge">
              {alert.kind === "irrigation_error"
                ? "Đã có xác nhận"
                : "Đã phục hồi"}
            </span>
          ) : (
            alert.trigger_type === "condition" && (
              <span className="badge">Đang diễn ra</span>
            )
          )}
        </div>
        <h3>{alert.title}</h3>
        <p>{alert.description}</p>
        <p className="muted small">
          Ghi nhận{" "}
          <time dateTime={alert.created_at}>
            {dateTime.format(new Date(alert.created_at))}
          </time>
          {alert.resolved_at && (
            <>
              {" "}
              · Cập nhật{" "}
              <time dateTime={alert.resolved_at}>
                {dateTime.format(new Date(alert.resolved_at))}
              </time>
            </>
          )}
        </p>
        <details className={styles.details}>
          <summary>Nguồn dữ liệu</summary>
          <p>
            <Link href={source.href}>{source.title}</Link>
            {" · "}
            {dateTime.format(new Date(alert.observed_at))}
            {" · "}
            {alert.data_source}
            {alert.source_ref && <> · Mã {alert.source_ref}</>}
            {alert.evidence.model_version && (
              <> · Model {alert.evidence.model_version}</>
            )}
          </p>
        </details>
      </div>
      {!alert.acknowledged_at && (
        <button
          type="button"
          className="button secondary"
          disabled={pending}
          onClick={() => acknowledge(alert.id)}
        >
          {pending ? "Đang lưu…" : "Đánh dấu đã xem"}
        </button>
      )}
    </li>
  );
}

export function AlertsView() {
  const { token } = usePlantcare();
  const [filter, setFilter] = useState<AlertState>("attention");
  const [cursors, setCursors] = useState<(string | null)[]>([null]);
  const { page, failed, refresh } = useAlerts(
    token,
    filter,
    cursors.at(-1) ?? null,
    20,
  );
  const [pending, setPending] = useState<string | null>(null);
  const [actionError, setActionError] = useState("");

  async function acknowledge(id: string) {
    if (pending) return;
    setPending(id);
    setActionError("");
    try {
      await request<Alert>(`/alerts/${id}/acknowledge`, token, {
        method: "POST",
      });
      await refresh();
    } catch (error) {
      setActionError(
        error instanceof Error ? error.message : "Chưa thể đánh dấu đã xem.",
      );
    } finally {
      setPending(null);
    }
  }

  return (
    <section className={`card ${styles.history}`} aria-label="Lịch sử cảnh báo">
      <div className={styles.toolbar}>
        <h2>Lịch sử cảnh báo</h2>
        {page && <span className="badge">{page.unread_count} chưa xem</span>}
        <label className={styles.filter}>
          Trạng thái
          <select
            value={filter}
            disabled={pending !== null}
            onChange={(event) => {
              setFilter(event.target.value as AlertState);
              setCursors([null]);
              setActionError("");
            }}
          >
            <option value="attention">Cần chú ý</option>
            <option value="unread">Chưa xem</option>
            <option value="all">Tất cả</option>
          </select>
        </label>
      </div>
      <AlertStatus failed={failed} page={page} />
      {actionError && (
        <p className="error" role="alert">
          {actionError}
        </p>
      )}
      <ul className={styles.items}>
        {page?.items.map((alert) => (
          <AlertItem
            key={alert.id}
            alert={alert}
            pending={pending === alert.id}
            acknowledge={acknowledge}
          />
        ))}
      </ul>
      {(cursors.length > 1 || page?.next_cursor) && (
        <nav className={styles.pagination} aria-label="Phân trang cảnh báo">
          <button
            type="button"
            className="button secondary"
            disabled={cursors.length === 1 || pending !== null}
            onClick={() => setCursors(cursors.slice(0, -1))}
          >
            Trang trước
          </button>
          <span>Trang {cursors.length}</span>
          <button
            type="button"
            className="button secondary"
            disabled={!page?.next_cursor || pending !== null}
            onClick={() => setCursors([...cursors, page?.next_cursor ?? null])}
          >
            Trang sau
          </button>
        </nav>
      )}
    </section>
  );
}
