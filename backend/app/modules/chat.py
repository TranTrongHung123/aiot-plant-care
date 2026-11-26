"""Grounded, read-only tools. The supervisor selects intents but cannot execute code."""

import re
from dataclasses import dataclass, field
from datetime import datetime
from time import perf_counter
from typing import Literal

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.schemas import DEVICE_ID, Readings, fresh
from app.db.models import Command, Device, Job, PlantImage, Policy, Reading
from app.integrations.chat_llm import Intent, Plan
from app.modules.alerts import AlertView, alert_view, read_alerts
from app.modules.knowledge import (
    IMAGE_REFERENCE,
    Citation,
    KnowledgeUnavailable,
    asks_management,
    lookup,
    normalize_query,
)
from app.modules.schedule import LOCAL, latest_slot
from app.modules.weather_control import active_weather

Status = Literal["ok", "insufficient_data", "read_only", "unsupported"]
IrrigationDecision = Literal["recommend", "wait", "insufficient_data"]


class Source(BaseModel):
    kind: Literal[
        "sensor", "device", "irrigation", "command", "weather", "image", "alert", "knowledge"
    ]
    ref: str | None = None
    timestamp: datetime | None = None
    data_source: str | None = None
    stale: bool = False
    model_version: str | None = None
    citation: Citation | None = Field(default=None, exclude_if=lambda value: value is None)


class ToolResult(BaseModel):
    intent: Intent
    agent: str
    status: Status = "ok"
    answer: str
    sources: list[Source]
    duration_ms: int = 0
    decision: IrrigationDecision | None = None


class SensorFact(BaseModel):
    id: str
    source: str
    measured_at: datetime
    values: Readings


class DeviceFact(BaseModel):
    id: str
    source: str
    last_seen: datetime | None
    online: bool
    pump: Literal["on", "off", "unknown"]


class IrrigationFact(BaseModel):
    enabled: bool
    threshold: float
    duration_seconds: int
    due: bool
    last_schedule_at: datetime | None
    last_schedule_status: str


class CommandFact(BaseModel):
    id: str
    action: Literal["pump_on", "pump_off"]
    status: str
    updated_at: datetime


class WeatherFact(BaseModel):
    mode: Literal["demo", "live"]
    available: bool
    source: str
    rain_expected: bool | None = None
    rain_probability: float | None = None
    model_version: str | None = None
    job_id: str | None = None
    forecast_start: datetime | None = None
    forecast_end: datetime | None = None


class ImageFact(BaseModel):
    id: str
    source: str
    created_at: datetime
    model_version: str
    newer_image: bool = False
    label: str | None = None
    class_name: str | None = None
    confidence: float | None = None
    counts: tuple[int, int] | None = None  # ripe, unripe


@dataclass
class Snapshot:
    sensor: SensorFact | None = None
    device: DeviceFact | None = None
    irrigation: IrrigationFact | None = None
    command: CommandFact | None = None
    weather: WeatherFact | None = None
    images: dict[str, ImageFact] = field(default_factory=dict)
    alerts: list[AlertView] = field(default_factory=list)
    connection_alerts: list[AlertView] = field(default_factory=list)
    reference_leaf: ImageFact | None = None


AGENTS = {
    "sensors": "iot",
    "irrigation": "irrigation",
    "volume": "irrigation",
    "weather": "weather",
    "leaf": "plant_health",
    "fruit": "plant_health",
    "pump": "supervisor",
    "other": "supervisor",
    "alerts": "iot",
    "connection_alerts": "iot",
    "knowledge": "knowledge",
}
PUMP_STATES = {"on": "bật", "off": "tắt", "unknown": "chưa xác nhận"}
COMMAND_STATES = {
    "requested": "đã gửi yêu cầu",
    "published": "đã chuyển lệnh tới MQTT",
    "accepted": "thiết bị đã nhận",
    "completed": "thiết bị đã báo hoàn tất",
    "rejected": "thiết bị đã từ chối",
    "failed": "thất bại",
    "timed_out": "hết thời gian chờ phản hồi",
}


def _time(value: datetime) -> str:
    return value.astimezone(LOCAL).strftime("%H:%M ngày %d/%m/%Y")


def _image_fact(db: Session, purpose: str, references: tuple[str, ...] = ()) -> ImageFact | None:
    base = (PlantImage.device_id == DEVICE_ID, PlantImage.purpose == purpose)
    query = (
        select(Job, PlantImage)
        .join(PlantImage, Job.image_id == PlantImage.id)
        .where(
            *base,
            Job.task == purpose,
            Job.provider == "trained",
            Job.status == "completed",
            Job.result["is_mock"].as_boolean().is_(False),
        )
        .order_by(PlantImage.created_at.desc(), Job.finished_at.desc(), Job.id.desc())
        .limit(1)
    )
    if references:
        query = query.where(PlantImage.id.in_(references))
    entry = db.execute(query).first()
    if entry is None:
        return None
    job, image = entry
    result = job.result
    fact = ImageFact(
        id=image.id,
        source=image.source,
        created_at=image.created_at,
        model_version=job.model_version,
        newer_image=not references
        and db.scalar(
            select(PlantImage.id)
            .where(*base)
            .order_by(PlantImage.created_at.desc(), PlantImage.id.desc())
            .limit(1)
        )
        != image.id,
    )
    if purpose == "leaf":
        label, confidence = result.get("label"), result.get("confidence")
        if isinstance(label, str) and isinstance(confidence, (int, float)) and 0 <= confidence <= 1:
            fact.label, fact.confidence = label, confidence
            class_name = result.get("class_name")
            if isinstance(class_name, str):
                fact.class_name = class_name
    else:
        detections = result.get("detections")
        if isinstance(detections, list) and all(
            isinstance(item, dict) and item.get("class_name") in {"ripe", "unripe"}
            for item in detections
        ):
            ripe = sum(item["class_name"] == "ripe" for item in detections)
            fact.counts = (ripe, len(detections) - ripe)
    return fact


def read_snapshot(
    db: Session,
    plan: Plan,
    at: datetime,
    *,
    mqtt_connected: bool,
    prior_images: tuple[str, ...] = (),
) -> Snapshot:
    intents = set(plan.intents)
    snapshot = Snapshot()
    if intents & {"sensors", "irrigation"}:
        device = db.get(Device, DEVICE_ID)
        if device:
            snapshot.device = DeviceFact(
                id=device.id,
                source=device.source,
                last_seen=device.last_seen,
                online=bool(mqtt_connected and device.online and fresh(device.last_seen, at)),
                pump=device.pump if device.pump in PUMP_STATES else "unknown",
            )
        reading = db.scalar(
            select(Reading)
            .where(Reading.device_id == DEVICE_ID)
            .order_by(Reading.measured_at.desc(), Reading.id.desc())
            .limit(1)
        )
        if reading:
            try:
                values = Readings.model_validate(reading.readings)
            except ValidationError:
                pass
            else:
                snapshot.sensor = SensorFact(
                    id=reading.id,
                    source=reading.source,
                    measured_at=reading.measured_at,
                    values=values,
                )
    if "irrigation" in intents:
        policy = db.get(Policy, 1)
        if policy:
            snapshot.irrigation = IrrigationFact(
                enabled=policy.enabled,
                threshold=policy.threshold,
                duration_seconds=policy.duration_seconds,
                due=latest_slot(policy, at) is not None,
                last_schedule_at=policy.last_schedule_at,
                last_schedule_status=policy.last_schedule_status,
            )
        command = db.scalar(
            select(Command)
            .where(Command.device_id == DEVICE_ID)
            .order_by(Command.issued_at.desc(), Command.id.desc())
            .limit(1)
        )
        if command:
            snapshot.command = CommandFact(
                id=command.id,
                action=command.action,
                status=command.status,
                updated_at=command.updated_at,
            )
    if "irrigation" in intents or ("weather" in intents and (plan.weather_minutes or 0) <= 60):
        snapshot.weather = WeatherFact.model_validate(active_weather(db, at))
    for purpose in intents & {"leaf", "fruit"}:
        if image := _image_fact(db, purpose, prior_images):
            snapshot.images[purpose] = image
    if "knowledge" in intents and prior_images:
        snapshot.reference_leaf = snapshot.images.get("leaf") or _image_fact(
            db, "leaf", prior_images
        )
    if "alerts" in intents:
        snapshot.alerts = [alert_view(row) for row in read_alerts(db, limit=5)]
    if "connection_alerts" in intents:
        rows = read_alerts(db, kinds=("device_offline", "sensor_stale"), limit=5)
        snapshot.connection_alerts = [alert_view(row) for row in rows]
    return snapshot


def _sensor_sources(snapshot: Snapshot, at: datetime) -> list[Source]:
    sources = []
    if snapshot.device:
        device = snapshot.device
        sources.append(
            Source(
                kind="device",
                ref=device.id,
                data_source=device.source,
                timestamp=device.last_seen,
                stale=not fresh(device.last_seen, at),
            )
        )
    if snapshot.sensor:
        reading = snapshot.sensor
        sources.append(
            Source(
                kind="sensor",
                ref=reading.id,
                data_source=reading.source,
                timestamp=reading.measured_at,
                stale=not fresh(reading.measured_at, at),
            )
        )
    return sources


def _sensors(snapshot: Snapshot, at: datetime) -> tuple[Status, str, list[Source]]:
    sources = _sensor_sources(snapshot, at)
    device, reading = snapshot.device, snapshot.sensor
    device_text = (
        f"Thiết bị online. Trạng thái bơm: {PUMP_STATES[device.pump]}."
        if device and device.online
        else "Thiết bị chưa xác nhận online."
    )
    if reading is None:
        return "insufficient_data", f"{device_text} Chưa có số đo cảm biến.", sources
    if not fresh(reading.measured_at, at):
        return (
            "insufficient_data",
            (f"{device_text} Số đo lúc {_time(reading.measured_at)} đã cũ."),
            sources,
        )
    values = reading.values
    soil = (
        f"{values.soil_moisture_pct:g}%"
        if values.soil_moisture_pct is not None
        else "chưa hiệu chuẩn"
    )
    return (
        "ok",
        (
            f"Số đo lúc {_time(reading.measured_at)}: {values.temperature_c:g} °C, "
            f"độ ẩm không khí {values.humidity_pct:g}%, độ ẩm đất {soil}. {device_text}"
        ),
        sources,
    )


def _weather(weather: WeatherFact | None, minutes: int | None) -> tuple[Status, str, list[Source]]:
    if minutes is not None and (minutes <= 0 or minutes > 60):
        return (
            "insufficient_data",
            ("Chưa có nguồn dự báo cho khoảng bạn hỏi. XGBoost chỉ dự báo theo cửa sổ 60 phút."),
            [],
        )
    if not weather or not weather.available:
        return "insufficient_data", "Chưa có dự báo mưa LIVE hợp lệ còn hạn.", []
    sources = [
        Source(
            kind="weather",
            ref=weather.job_id,
            timestamp=weather.forecast_start,
            data_source=weather.source,
            model_version=weather.model_version,
        )
    ]
    if weather.mode == "demo":
        scenario = "có mưa" if weather.rain_expected else "không mưa"
        return (
            "insufficient_data",
            f"Chế độ DEMO đang mô phỏng {scenario}. Cần dự báo LIVE để biết thời tiết thực.",
            sources,
        )
    if (
        weather.forecast_start is None
        or weather.forecast_end is None
        or weather.rain_probability is None
    ):
        return "insufficient_data", "Dự báo LIVE thiếu cửa sổ hoặc xác suất mưa.", sources
    return (
        "ok",
        (
            f"XGBoost ước tính khả năng mưa {weather.rain_probability:.1%} "
            f"từ {_time(weather.forecast_start)} đến {_time(weather.forecast_end)} "
            "theo dữ liệu quan trắc OpenWeather."
        ),
        sources,
    )


def _irrigation(
    snapshot: Snapshot, at: datetime
) -> tuple[Status, str, list[Source], IrrigationDecision]:
    sources = _sensor_sources(snapshot, at)
    policy, sensor, device, weather = (
        snapshot.irrigation,
        snapshot.sensor,
        snapshot.device,
        snapshot.weather,
    )
    soil = sensor.values.soil_moisture_pct if sensor else None
    decision = "wait"
    _, weather_text, weather_sources = _weather(weather, None)
    sources.extend(weather_sources)
    if policy:
        sources.append(Source(kind="irrigation", ref="1", timestamp=policy.last_schedule_at))
    if not policy:
        status, reason = "insufficient_data", "Chưa có chính sách tưới."
    elif not policy.enabled:
        status, reason = "ok", "Lịch tưới tự động đang tắt."
    elif not device or not device.online:
        status, reason = "insufficient_data", "Thiết bị chưa xác nhận online, nên chờ cập nhật."
    elif not sensor or not fresh(sensor.measured_at, at):
        status, reason = "insufficient_data", "Cần số đo độ ẩm đất còn mới trước khi tư vấn."
    elif soil is None:
        status, reason = "insufficient_data", "Cảm biến độ ẩm đất chưa hiệu chuẩn."
    elif soil >= policy.threshold:
        status, reason = "ok", f"Độ ẩm đất {soil:g}% đạt ngưỡng {policy.threshold:g}%. Nên chờ."
    elif (
        not weather
        or weather.mode != "live"
        or not weather.available
        or weather.rain_expected is None
    ):
        status, reason = (
            "insufficient_data",
            f"Độ ẩm đất {soil:g}% dưới ngưỡng {policy.threshold:g}%. {weather_text}",
        )
    elif weather.rain_expected:
        status, reason = "ok", "Dự báo LIVE báo có mưa trong cửa sổ hiện tại. Nên chờ."
    elif device.pump != "off":
        status, reason = "ok", "Bơm chưa xác nhận tắt. Nên chờ trạng thái tiếp theo."
    elif snapshot.command and snapshot.command.status in {"requested", "published", "accepted"}:
        status, reason = "ok", "Đang chờ lệnh tưới gần nhất có kết quả. Nên chờ phản hồi."
    elif not policy.due:
        status, reason = "ok", "Độ ẩm đất dưới ngưỡng và chưa đến cửa sổ tưới theo lịch."
    else:
        decision = "recommend"
        status, reason = (
            "ok",
            (
                "Đất dưới ngưỡng và lịch đang đến hạn. "
                "Bộ điều khiển sẽ kiểm tra an toàn trước khi tưới."
            ),
        )
    if snapshot.command:
        command = snapshot.command
        sources.append(Source(kind="command", ref=command.id, timestamp=command.updated_at))
        action = "bật" if command.action == "pump_on" else "tắt"
        state = COMMAND_STATES.get(command.status, "đang chờ phản hồi")
        reason += f" Lệnh {action} bơm gần nhất lúc {_time(command.updated_at)}: {state}."
    return (
        status,
        reason,
        sources,
        "insufficient_data" if status == "insufficient_data" else decision,
    )


def _plant(image: ImageFact | None, intent: Intent) -> tuple[Status, str, list[Source]]:
    if (
        image is None
        or (intent == "leaf" and (image.label is None or image.confidence is None))
        or (intent == "fruit" and image.counts is None)
    ):
        subject = "lá" if intent == "leaf" else "quả"
        return "insufficient_data", f"Chưa có kết quả ảnh {subject} đã phân tích hợp lệ.", []
    sources = [
        Source(
            kind="image",
            ref=image.id,
            timestamp=image.created_at,
            data_source=image.source,
            model_version=image.model_version,
        )
    ]
    if intent == "leaf":
        result = (
            f"MobileNetV3 nghiêng về nhãn “{image.label}” với điểm {image.confidence:.1%}. "
            "Điểm này chưa được hiệu chuẩn, nên mình dùng nhãn như một gợi ý để đối chiếu."
        )
    else:
        ripe, unripe = image.counts
        result = (
            f"YOLO phát hiện {ripe + unripe} quả trong ảnh, gồm {ripe} quả chín và "
            f"{unripe} quả xanh. Mô hình này chỉ phân loại độ chín."
        )
    older = " Có ảnh mới hơn chưa có kết quả hợp lệ." if image.newer_image else ""
    return "ok", f"Ảnh lúc {_time(image.created_at)}. {result}{older}", sources


def _alerts(rows: list[AlertView], connection_only: bool) -> tuple[Status, str, list[Source]]:
    if not rows:
        topic = " về kết nối hoặc số đo cũ" if connection_only else ""
        return "ok", f"Hiện chưa có cảnh báo{topic} đang cần chú ý.", []
    answer = "\n".join(
        f"{alert.title}, ghi nhận lúc {_time(alert.created_at)}. {alert.description}"
        for alert in rows
    )
    sources = [
        Source(
            kind="alert",
            ref=alert.id,
            timestamp=alert.observed_at,
            data_source=alert.data_source,
            model_version=alert.evidence.model_version,
        )
        for alert in rows
    ]
    return "ok", answer, sources


def _knowledge(
    question: str,
    leaf_context: bool,
    image: ImageFact | None,
    prior_refs: tuple[str, ...],
    prior_question: str,
    reference_leaf: ImageFact | None,
):
    referenced = False
    wants_image = reference_leaf is not None and bool(
        IMAGE_REFERENCE.search(normalize_query(question))
    )
    try:
        hits = lookup(
            question,
            leaf_context=leaf_context,
            leaf_label=image.class_name if leaf_context and image else None,
            prior_refs=() if wants_image else prior_refs,
        )
        if not hits and not leaf_context and reference_leaf and reference_leaf.class_name:
            hits = lookup(question, leaf_context=True, leaf_label=reference_leaf.class_name)
            referenced = bool(hits)
        if (
            not hits
            and not leaf_context
            and not wants_image
            and prior_question
            and not (asks_management(question) or asks_management(prior_question))
        ):
            hits = lookup(prior_question)
    except KnowledgeUnavailable:
        return "insufficient_data", "Kho tài liệu đang tạm thời không khả dụng.", []
    if not hits:
        if asks_management(question):
            return (
                "insufficient_data",
                "Mình chưa tìm được hướng dẫn xử lý phù hợp cho trường hợp này.",
                [],
            )
        return "insufficient_data", "Chưa tìm được tài liệu phù hợp để hướng dẫn thêm.", []
    answer = "\n\n".join(f"{text} [{index}]" for index, (_, text, _) in enumerate(hits, 1))
    if re.search(r"\b(?:nen chup|chup can|chup lai|chup phan nao)\b", normalize_query(question)):
        answer += (
            "\n\nBạn có thể chụp cận một lá có vết rõ, lấy nét vào mép vết và chụp cả hai mặt lá."
        )
    sources = [Source(kind="knowledge", ref=ref, citation=citation) for ref, _, citation in hits]
    if referenced and reference_leaf:
        sources[:0] = _plant(reference_leaf, "leaf")[2]
    return "ok", answer, sources


def execute_plan(
    plan: Plan,
    snapshot: Snapshot,
    at: datetime,
    *,
    question: str = "",
    prior_refs: tuple[str, ...] = (),
    prior_question: str = "",
) -> list[ToolResult]:
    results = []
    intents = tuple(dict.fromkeys(plan.intents))
    for intent in sorted(intents, key=lambda item: item == "knowledge"):
        started = perf_counter()
        decision = None
        match intent:
            case "sensors":
                status, answer, sources = _sensors(snapshot, at)
            case "irrigation":
                status, answer, sources, decision = _irrigation(snapshot, at)
            case "weather":
                status, answer, sources = _weather(snapshot.weather, plan.weather_minutes)
            case "leaf" | "fruit":
                status, answer, sources = _plant(snapshot.images.get(intent), intent)
            case "alerts":
                status, answer, sources = _alerts(snapshot.alerts, connection_only=False)
            case "connection_alerts":
                status, answer, sources = _alerts(snapshot.connection_alerts, connection_only=True)
            case "knowledge":
                status, answer, sources = _knowledge(
                    question,
                    "leaf" in intents,
                    snapshot.images.get("leaf"),
                    prior_refs,
                    prior_question,
                    snapshot.reference_leaf,
                )
            case "volume":
                status, answer, sources = (
                    "insufficient_data",
                    ("Cần đo hoặc hiệu chuẩn lưu lượng bơm trước khi ước lượng lượng nước."),
                    [],
                )
            case "pump":
                preview = ""
                if plan.pump_action == "off":
                    preview = "Yêu cầu xem trước: tắt bơm. "
                elif plan.pump_action == "on" and plan.pump_seconds is not None:
                    if 1 <= plan.pump_seconds <= 15:
                        preview = f"Yêu cầu xem trước: bật bơm {plan.pump_seconds} giây. "
                    else:
                        preview = "Thời lượng yêu cầu nằm ngoài giới hạn 1–15 giây. "
                status, answer, sources = (
                    "read_only",
                    (
                        f"{preview}Trợ lý hiện chỉ đọc dữ liệu và tư vấn. "
                        "Cần dùng mục Điều khiển tưới nếu bạn muốn gửi và theo dõi lệnh bơm."
                    ),
                    [],
                )
            case _:
                status, answer, sources = (
                    "unsupported",
                    (
                        "Mình có thể hỗ trợ về số đo, lịch tưới, dự báo mưa "
                        "hoặc kết quả ảnh của vườn."
                    ),
                    [],
                )
        results.append(
            ToolResult(
                intent=intent,
                agent=AGENTS[intent],
                status=status,
                answer=answer,
                sources=sources,
                duration_ms=round((perf_counter() - started) * 1000),
                decision=decision,
            )
        )
    return results
