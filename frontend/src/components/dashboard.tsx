"use client";
import { FormEvent, useEffect, useState } from "react";
import { API, Dashboard, Job, Policy, time } from "@/lib/api";

export function Icon({ name, size = 22 }: { name: string; size?: number }) {
  const paths: Record<string, string> = {
    leaf: "M20 4C8 2 2 8 5 15c3 7 15 5 15-11ZM5 20 16 9M10 15v-5m0 5h5",
    grid: "M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z",
    drop: "M12 3S5 11 5 15a7 7 0 0 0 14 0c0-4-7-12-7-12ZM8 15a4 4 0 0 0 4 4",
    sun: "M12 3v2m0 14v2M3 12h2m14 0h2M5 5l2 2m10 10 2 2M5 19l2-2M17 7l2-2M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0",
    image: "M3 4h18v16H3zM3 16l6-6 5 5 3-3 4 4M16 8h.01",
    clock: "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0M12 7v5l3 2",
    upload: "M12 16V3m-5 5 5-5 5 5M4 16v5h16v-5",
    cloud:
      "M6 17a5 5 0 0 1 0-10 7 7 0 0 1 13 2 4 4 0 0 1 0 8ZM8 20v1m5-1v1m5-1v1",
    bolt: "m13 2-9 12h7l-1 8L21 9h-8z",
  };
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={paths[name] ?? paths.leaf} />
    </svg>
  );
}
export function PlantArt() {
  return (
    <svg className="plant-art" viewBox="0 0 240 220" aria-hidden="true">
      <ellipse cx="126" cy="207" rx="69" ry="10" fill="#1b583c" opacity=".12" />
      <path d="M97 159h64l-10 43h-44z" fill="#d5a783" />
      <path d="M92 151h74v14H92z" fill="#e7bb98" />
      <path
        d="M129 154c-6-54 2-91 7-124m-8 89-40-34m42 11 37-32m-37 68 46-13"
        fill="none"
        stroke="#2c6945"
        strokeWidth="5"
      />
      <path
        d="M132 68c-39 1-50-22-44-42 29-2 46 16 44 42M136 58c1-30 17-42 42-38-2 29-17 40-42 38M113 104c-34 9-52-4-58-28 33-8 51 4 58 28M149 87c-1-26 17-38 43-34-5 27-22 35-43 34M149 134c13-28 40-27 58-12-16 22-37 27-58 12"
        fill="#4b8961"
      />
      <circle cx="95" cy="121" r="22" fill="#e5684f" />
      <circle cx="165" cy="103" r="18" fill="#ef8963" />
      <path
        d="m85 100 11 5 11-5-7 11H91zM155 86l10 4 9-4-6 10h-7z"
        fill="#2c6945"
      />
      <path
        d="M84 122c0-7 3-10 6-11M156 102c0-5 2-8 4-9"
        stroke="#ffc5a7"
        strokeWidth="3"
        strokeLinecap="round"
        fill="none"
      />
    </svg>
  );
}
export function ProtectedImage({ job, token }: { job: Job; token: string }) {
  const [url, setUrl] = useState("");
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    let objectUrl = "";
    fetch(`${API}/images/${job.image_id}/content`, {
      headers: { Authorization: `Bearer ${token}` },
      signal: controller.signal,
    })
      .then((r) => {
        if (!r.ok) throw new Error();
        return r.blob();
      })
      .then((blob) => {
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
      })
      .catch(() => {
        if (!controller.signal.aborted) setFailed(true);
      });
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [job.image_id, token]);
  return (
    <div className="image-frame">
      {url ? (
        <div className="image-canvas">
          {/* Authenticated blob URLs bypass the image optimizer. */}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={url} alt="Ảnh cây đã tải lên" />
          {job.result?.detections?.map((box, i) => (
            <div
              className="detection"
              key={i}
              style={{
                left: `${box.box[0] * 100}%`,
                top: `${box.box[1] * 100}%`,
                width: `${box.box[2] * 100}%`,
                height: `${box.box[3] * 100}%`,
              }}
            >
              <span>
                {box.label} · {Math.round(box.confidence * 100)}%
                {job.result?.is_mock ? " · Mock cũ" : ""}
              </span>
            </div>
          ))}
        </div>
      ) : (
        <span>{failed ? "Không tải được ảnh" : "Đang tải ảnh…"}</span>
      )}
    </div>
  );
}
export function Trend({ data }: { data: Dashboard }) {
  const points = data.history.map((r, i) =>
    r.readings.soil_moisture_pct === null
      ? null
      : `${20 + (i * 620) / Math.max(1, data.history.length - 1)},${160 - r.readings.soil_moisture_pct * 1.3}`,
  );
  return (
    <div className="chart">
      <svg
        viewBox="0 0 680 205"
        role="img"
        aria-label="Độ ẩm đất của 60 bản ghi gần nhất"
      >
        {[0, 25, 50, 75, 100].map((v) => (
          <g key={v}>
            <line
              x1="20"
              x2="640"
              y1={160 - v * 1.3}
              y2={160 - v * 1.3}
              stroke="#e9eee9"
              strokeDasharray="4 5"
            />
            <text x="650" y={165 - v * 1.3}>
              {v}%
            </text>
          </g>
        ))}
        <line
          x1="20"
          x2="640"
          y1={160 - data.policy.threshold * 1.3}
          y2={160 - data.policy.threshold * 1.3}
          stroke="#d6a15d"
          strokeDasharray="6 5"
        />
        <polyline
          points={points.filter(Boolean).join(" ")}
          fill="none"
          stroke="#408266"
          strokeWidth="3"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        {data.history.length > 0 && (
          <>
            <text x="20" y="193">
              {time(data.history[0].measured_at)}
            </text>
            <text x="565" y="193">
              {time(data.history.at(-1)!.measured_at)}
            </text>
          </>
        )}
      </svg>
      {!data.history.length && <p className="empty">Đang chờ simulator…</p>}
    </div>
  );
}
export function PolicyForm({
  policy,
  save,
  busy,
}: {
  policy: Policy;
  save: (p: Policy) => void;
  busy: boolean;
}) {
  const [scheduleKind, setScheduleKind] = useState(policy.schedule_kind);
  const submit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    save({
      ...policy,
      enabled: f.has("enabled"),
      threshold: Number(f.get("threshold")),
      duration_seconds: Number(f.get("duration")),
      cooldown_seconds: Number(f.get("cooldown")),
      schedule_kind: scheduleKind,
      daily_times: String(f.get("daily_times") ?? policy.daily_times.join(","))
        .split(",")
        .map((value) => value.trim())
        .filter(Boolean),
      interval_minutes: Number(
        f.get("interval_minutes") ?? policy.interval_minutes,
      ),
    });
  };
  return (
    <form onSubmit={submit}>
      <label className="check-label">
        <input name="enabled" type="checkbox" defaultChecked={policy.enabled} />{" "}
        Bật tự động theo lịch, có kiểm tra thời tiết
      </label>
      <p className="small muted">Tắt tự động để dùng chế độ tưới thủ công.</p>
      <label>
        Kiểu lịch lặp
        <select
          aria-label="Kiểu lịch lặp"
          value={scheduleKind}
          onChange={(event) =>
            setScheduleKind(event.target.value as "daily" | "interval")
          }
        >
          <option value="daily">Hằng ngày theo giờ</option>
          <option value="interval">Lặp theo chu kỳ phút</option>
        </select>
      </label>
      {scheduleKind === "daily" ? (
        <label>
          Giờ tưới hằng ngày (giờ Việt Nam)
          <input
            name="daily_times"
            defaultValue={policy.daily_times.join(", ")}
            placeholder="06:00, 18:00"
            required
          />
          <span className="small muted">
            Định dạng HH:MM, cách nhau bằng dấu phẩy.
          </span>
        </label>
      ) : (
        <label>
          Chu kỳ (phút)
          <input
            name="interval_minutes"
            type="number"
            min="1"
            max="1440"
            defaultValue={policy.interval_minutes}
            required
          />
          <span className="small muted">
            Lần đầu sau một chu kỳ tính từ lúc lưu thay đổi.
          </span>
        </label>
      )}
      <div className="form-grid">
        <label>
          Ngưỡng đất (%)
          <input
            name="threshold"
            type="number"
            min="10"
            max="60"
            defaultValue={policy.threshold}
            required
          />
        </label>
        <label>
          Thời lượng (giây)
          <input
            name="duration"
            type="number"
            min="1"
            max="15"
            defaultValue={policy.duration_seconds}
            required
          />
        </label>
        <label>
          Nghỉ giữa lần tưới (giây)
          <input
            name="cooldown"
            type="number"
            min="30"
            max="3600"
            defaultValue={policy.cooldown_seconds}
            required
          />
        </label>
      </div>
      <button className="button secondary" disabled={busy}>
        Lưu quy tắc
      </button>
      <p className="muted small">
        Đến lịch: đất phải khô, thời tiết cho phép và đủ điều kiện an toàn. AI
        hỗ trợ quy tắc; không trực tiếp bật bơm. Không tưới bù lịch đã bỏ lỡ.
      </p>
      {policy.last_schedule_at && (
        <p className="small muted">
          Lần lịch gần nhất: {time(policy.last_schedule_at)} ·{" "}
          {policy.last_schedule_reason}
        </p>
      )}
    </form>
  );
}
