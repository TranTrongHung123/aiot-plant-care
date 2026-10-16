from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select

from app.core.schemas import DEVICE_ID, PumpRequest, fresh
from app.db.models import Command, Device, Policy, Reading, now
from app.db.session import Session
from app.modules.schedule import due_slot, latest_slot
from app.modules.weather_control import active_weather

ACTIVE = ("requested", "published", "accepted")


def event(command, status, reason=""):
    command.status, command.reason, command.updated_at = status, reason, now()
    command.events = [
        *command.events,
        {
            "status": status,
            "reason": reason,
            "at": command.updated_at.isoformat(),
        },
    ]


def create_command(request: PumpRequest, bridge, origin="manual", schedule_at=None):
    at = now()
    with Session.begin() as db:
        device = db.scalar(select(Device).where(Device.id == DEVICE_ID).with_for_update())
        existing = db.get(Command, str(request.command_id))
        if existing:
            if (
                existing.action != request.action
                or existing.duration_ms != request.duration_seconds * 1000
            ):
                raise HTTPException(409, "Command ID đã dùng cho nội dung khác")
            return existing
        if not bridge.connected:
            raise HTTPException(503, "MQTT chưa kết nối; chưa gửi lệnh")
        weather_context = {"control": "manual", "weather_check": "operator_override"}
        if request.action == "pump_on":
            reading = db.scalar(select(Reading).order_by(Reading.measured_at.desc()).limit(1))
            if not device or not device.online or not fresh(device.last_seen, at):
                raise HTTPException(409, "Thiết bị ngoại tuyến hoặc trạng thái đã cũ")
            if not reading or not fresh(reading.measured_at, at):
                raise HTTPException(409, "Dữ liệu cảm biến đã cũ")
            if reading.readings.get("soil_moisture_pct") is None:
                raise HTTPException(409, "Cảm biến đất chưa hiệu chuẩn")
            if device.pump != "off":
                raise HTTPException(409, "Bơm chưa xác nhận OFF")
            active = db.scalar(select(Command).where(Command.status.in_(ACTIVE)).limit(1))
            if active:
                raise HTTPException(409, "Đang chờ lệnh trước hoàn tất")
            policy = db.scalar(select(Policy).where(Policy.id == 1).with_for_update())
            if origin == "automatic":
                if not policy.enabled or reading.readings["soil_moisture_pct"] >= policy.threshold:
                    raise HTTPException(409, "Quy tắc tự động đã đổi; hủy yêu cầu tưới")
                if request.duration_seconds != policy.duration_seconds:
                    raise HTTPException(409, "Thời lượng tự động đã đổi; chờ đánh giá lại")
                if (
                    schedule_at is None
                    or policy.last_schedule_at != schedule_at
                    or latest_slot(policy, at) != schedule_at
                ):
                    raise HTTPException(409, "Lịch tưới đã đổi hoặc hết cửa sổ chạy")
                weather_context = active_weather(db, at)
                if not weather_context["available"] or weather_context["rain_expected"]:
                    raise HTTPException(409, weather_context["reason"])
                weather_context = {**weather_context, "schedule_at": schedule_at.isoformat()}
            previous = db.scalar(
                select(Command)
                .where(Command.action == "pump_on")
                .order_by(Command.issued_at.desc())
                .limit(1)
            )
            if previous and (at - previous.updated_at).total_seconds() < policy.cooldown_seconds:
                raise HTTPException(409, "Chưa hết thời gian nghỉ giữa hai lần tưới")
        command = Command(
            id=str(request.command_id),
            device_id=DEVICE_ID,
            action=request.action,
            duration_ms=request.duration_seconds * 1000,
            origin=origin,
            issued_at=at,
            expires_at=at + timedelta(seconds=10),
            events=[],
            weather_context=weather_context,
        )
        event(command, "published", "Đã ghi nhận, đang gửi qua MQTT")
        db.add(command)
    payload = {
        "schema_version": 1,
        "command_id": command.id,
        "device_id": DEVICE_ID,
        "source": "simulation",
        "issued_at": at.isoformat(),
        "expires_at": command.expires_at.isoformat(),
        "action": command.action,
        "duration_ms": command.duration_ms,
    }
    try:
        bridge.publish("command", payload)
    except RuntimeError:
        with Session.begin() as db:
            item = db.get(Command, command.id)
            if item.status == "published":
                event(item, "failed", "Không gửi được MQTT")
        raise HTTPException(503, "Không gửi được MQTT") from None
    return command


def maintain(bridge):
    at = now()
    with Session.begin() as db:
        db.scalar(select(Device).where(Device.id == DEVICE_ID).with_for_update())
        for command in db.scalars(
            select(Command).where(Command.status.in_(ACTIVE)).with_for_update()
        ):
            deadline = command.expires_at + timedelta(milliseconds=command.duration_ms + 5000)
            if at > deadline:
                event(command, "timed_out", "Chưa nhận ACK hoàn tất; không suy diễn bơm OFF")
        policy = db.scalar(select(Policy).where(Policy.id == 1).with_for_update())
        reading = db.scalar(select(Reading).order_by(Reading.measured_at.desc()).limit(1))
        should_water = False
        slot = due_slot(policy, at)
        if not policy.enabled:
            policy.decision = "Chế độ thủ công; lịch tự động đang tắt"
        elif slot is None:
            if (
                policy.last_schedule_status == "waiting_weather"
                and latest_slot(policy, at) != policy.last_schedule_at
            ):
                policy.last_schedule_status = "skipped"
                policy.last_schedule_reason = "Bỏ qua lịch: hết thời gian chờ dự báo LIVE hợp lệ"
            policy.decision = "Đang chờ lịch tiếp theo"
            if policy.last_schedule_reason:
                policy.decision += f". Lần gần nhất: {policy.last_schedule_reason}"
        else:
            # Claim once in DB before releasing the lock; never replay after restart.
            policy.last_schedule_at = slot
            policy.last_schedule_status = "skipped"
            weather = active_weather(db, at)
            if not reading or not fresh(reading.measured_at, at):
                policy.decision = "Không tưới: cảm biến đã cũ"
            elif reading.readings.get("soil_moisture_pct") is None:
                policy.decision = "Không tưới: chưa hiệu chuẩn cảm biến đất"
            elif reading.readings["soil_moisture_pct"] >= policy.threshold:
                policy.decision = "Độ ẩm đất đủ; bỏ qua lần tưới này"
            elif not weather["available"]:
                policy.last_schedule_status = "waiting_weather"
                policy.decision = weather["reason"] + "; đang chờ trong cửa sổ lịch"
            elif weather["rain_expected"]:
                policy.decision = weather["reason"]
            else:
                should_water = True
                policy.last_schedule_status = "checking"
                policy.decision = "Đang kiểm tra an toàn trước khi gửi lệnh theo lịch"
            policy.last_schedule_reason = policy.decision
        duration = policy.duration_seconds
    if should_water:
        from uuid import uuid4

        try:
            create_command(
                PumpRequest(command_id=uuid4(), action="pump_on", duration_seconds=duration),
                bridge,
                "automatic",
                schedule_at=slot,
            )
            decision = "Đã yêu cầu tưới theo lịch: đất khô, không mưa, đủ điều kiện an toàn"
        except HTTPException as exc:
            decision = f"Không tưới: {exc.detail}"
        with Session.begin() as db:
            policy = db.get(Policy, 1)
            if policy.last_schedule_at == slot:
                policy.decision = policy.last_schedule_reason = decision
                policy.last_schedule_status = (
                    "requested" if not decision.startswith("Không tưới") else "skipped"
                )
