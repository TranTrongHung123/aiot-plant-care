"""Lịch lặp Asia/Ho_Chi_Minh; không tưới bù những lịch đã bỏ lỡ."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

LOCAL = ZoneInfo("Asia/Ho_Chi_Minh")


def latest_slot(policy, at):
    if not policy.enabled or at < policy.schedule_started_at:
        return None
    if policy.schedule_kind == "interval":
        period = timedelta(minutes=policy.interval_minutes)
        steps = (at - policy.schedule_started_at) // period
        if steps < 1:
            return None
        slot = policy.schedule_started_at + steps * period
        grace = min(period, timedelta(minutes=5))
    else:
        local = at.astimezone(LOCAL)
        candidates = [
            datetime.combine(
                local.date() - timedelta(days=day), datetime.strptime(value, "%H:%M").time(), LOCAL
            ).astimezone(UTC)
            for day in (0, 1)
            for value in policy.daily_times
        ]
        candidates = [slot for slot in candidates if policy.schedule_started_at < slot <= at]
        if not candidates:
            return None
        slot, grace = max(candidates), timedelta(minutes=5)
    return slot if at - slot < grace else None


def due_slot(policy, at):
    slot = latest_slot(policy, at)
    if slot is not None and (
        policy.last_schedule_at is None
        or slot > policy.last_schedule_at
        or (slot == policy.last_schedule_at and policy.last_schedule_status == "waiting_weather")
    ):
        return slot
    return None
