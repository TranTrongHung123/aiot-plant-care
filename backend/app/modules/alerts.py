"""Shared alert views for the dashboard and read-only chat tools."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import or_, select, tuple_
from sqlalchemy.orm import Session

from app.core.schemas import DEVICE_ID
from app.db.models import AgentAlert

AlertState = Literal["attention", "unread", "all"]

TITLES = {
    "device_offline": "Mất kết nối thiết bị",
    "sensor_stale": "Số đo cảm biến đã cũ",
    "low_soil": "Đất khô kéo dài",
    "irrigation_error": "Lệnh tưới cần kiểm tra",
    "leaf_review": "Ảnh lá cần xem lại",
}
CONNECTION_REASONS = {
    "mqtt_unavailable": "Backend mất kết nối MQTT tại thời điểm cảnh báo.",
    "offline_reported": "Thiết bị đã gửi trạng thái ngoại tuyến.",
    "status_expired": "Thiết bị không còn gửi trạng thái trong khoảng theo dõi.",
}


class AlertEvidence(BaseModel):
    reason: str | None = None
    status: str | None = None
    action: str | None = None
    soil: float | None = None
    threshold: float | None = None
    duration_seconds: int | None = None
    window_start: datetime | None = None
    class_name: str | None = None
    label: str | None = None
    score: float | None = None
    model_version: str | None = None
    job_id: str | None = None
    image_created_at: datetime | None = None


class AlertView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: Literal["device_offline", "sensor_stale", "low_soil", "irrigation_error", "leaf_review"]
    trigger_type: Literal["condition", "event"]
    severity: Literal["warning", "info"]
    source_kind: Literal["device", "reading", "command", "image"]
    source_ref: str | None
    data_source: str
    observed_at: datetime
    created_at: datetime
    acknowledged_at: datetime | None
    resolved_at: datetime | None
    evidence: AlertEvidence
    title: str
    description: str


def alert_view(row: AgentAlert) -> AlertView:
    detail = row.evidence
    match row.kind:
        case "device_offline":
            description = CONNECTION_REASONS.get(
                detail.get("reason"), "Kiểm tra trạng thái thiết bị."
            )
        case "sensor_stale":
            description = "Thiết bị còn online nhưng chưa gửi số đo mới trong khoảng theo dõi."
        case "low_soil":
            description = (
                f"Độ ẩm đất {detail['soil']:g}% dưới ngưỡng {detail['threshold']:g}% "
                f"trong {detail['duration_seconds']} giây."
            )
        case "irrigation_error":
            if detail.get("status") == "timed_out":
                description = "Đã hết thời gian chờ phản hồi xác nhận kết quả lệnh tưới."
            else:
                description = "Thiết bị đã báo lệnh tưới thất bại."
            if row.resolved_at:
                description += " Thiết bị đã gửi xác nhận hoàn tất sau đó."
        case "leaf_review":
            description = (
                f"MobileNetV3 gợi ý nhãn {detail['label']} với điểm mô hình "
                f"{detail['score']:.1%}. Hãy xem lại ảnh lá."
            )
    return AlertView.model_validate(
        {
            "id": row.id,
            "kind": row.kind,
            "trigger_type": row.trigger_type,
            "severity": row.severity,
            "source_kind": row.source_kind,
            "source_ref": row.source_ref,
            "data_source": row.data_source,
            "observed_at": row.observed_at,
            "created_at": row.created_at,
            "acknowledged_at": row.acknowledged_at,
            "resolved_at": row.resolved_at,
            "evidence": detail,
            "title": TITLES[row.kind],
            "description": description,
        }
    )


def read_alerts(
    db: Session,
    state: AlertState = "attention",
    *,
    cursor: tuple[datetime, str] | None = None,
    limit: int = 25,
    kinds: tuple[str, ...] = (),
) -> list[AgentAlert]:
    statement = select(AgentAlert).where(AgentAlert.device_id == DEVICE_ID)
    if kinds:
        statement = statement.where(AgentAlert.kind.in_(kinds))
    if state == "attention":
        statement = statement.where(
            AgentAlert.resolved_at.is_(None),
            or_(AgentAlert.trigger_type == "condition", AgentAlert.acknowledged_at.is_(None)),
        )
    elif state == "unread":
        statement = statement.where(AgentAlert.acknowledged_at.is_(None))
    if cursor:
        statement = statement.where(tuple_(AgentAlert.created_at, AgentAlert.id) < cursor)
    return list(
        db.scalars(
            statement.order_by(AgentAlert.created_at.desc(), AgentAlert.id.desc()).limit(limit)
        )
    )
