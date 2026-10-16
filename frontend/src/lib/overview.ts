import { Policy } from "./api";

// Chỉ tính mốc hiển thị. Backend vẫn quyết định có thực thi lịch hay không.
export function nextSchedule(
  policy: Policy,
  serverTime: string,
): string | null {
  if (!policy.enabled) return null;
  const now = Date.parse(serverTime);
  const start = Date.parse(policy.schedule_started_at);
  if (!Number.isFinite(now) || !Number.isFinite(start)) return null;
  if (policy.schedule_kind === "interval") {
    const period = policy.interval_minutes * 60_000;
    if (period <= 0) return null;
    return new Date(
      start + Math.max(1, Math.floor((now - start) / period) + 1) * period,
    ).toISOString();
  }
  const localDate = new Date(now + 7 * 3_600_000).toISOString().slice(0, 10);
  const candidates = policy.daily_times
    .flatMap((value) => {
      const today = Date.parse(`${localDate}T${value}:00+07:00`);
      return [today, today + 86_400_000];
    })
    .filter((value) => Number.isFinite(value) && value > Math.max(now, start));
  return candidates.length
    ? new Date(Math.min(...candidates)).toISOString()
    : null;
}
