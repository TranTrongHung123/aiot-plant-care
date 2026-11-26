"""Periodic monitoring rules, backed by durable evidence and checkpoints."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from pydantic import ValidationError
from sqlalchemy import func, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session as DatabaseSession

from app.core.schemas import DEVICE_ID, Readings, fresh
from app.core.settings import Settings, settings
from app.db.models import (
    AgentAlert,
    Command,
    Device,
    Job,
    MonitoringCheckpoint,
    PlantImage,
    Policy,
    Reading,
)
from app.db.session import Session
from app.modules.trained_ai import LEAF_LABELS

FRESH_SECONDS = 20
EVENT_BATCH = 100
EVENT_OVERLAP = timedelta(seconds=30)
CONDITION_KINDS = ("device_offline", "sensor_stale", "low_soil")


@dataclass(frozen=True)
class Evidence:
    source_kind: str
    source_ref: str
    data_source: str
    observed_at: datetime
    details: dict


@dataclass(frozen=True)
class Sample:
    id: str
    source: str
    at: datetime
    soil: float | None
    valid: bool = True


@dataclass(frozen=True)
class Snapshot:
    device_id: str
    source: str
    last_seen: datetime | None
    online: bool
    mqtt_connected: bool
    threshold: float | None
    samples: tuple[Sample, ...]  # newest first


@dataclass
class Evaluation:
    opening: dict[str, Evidence] = field(default_factory=dict)
    recovered: dict[str, datetime] = field(default_factory=dict)


def _dry_start(samples: tuple[Sample, ...], threshold: float) -> datetime:
    first = samples[0].at
    for sample in samples[1:]:
        if (
            first - sample.at > timedelta(seconds=FRESH_SECONDS)
            or sample.soil is None
            or sample.soil >= threshold
        ):
            break
        first = sample.at
    return first


def evaluate_conditions(
    snapshot: Snapshot, at: datetime, config: Settings = settings
) -> Evaluation:
    result = Evaluation()
    last_seen = snapshot.last_seen
    online = bool(snapshot.mqtt_connected and snapshot.online and fresh(last_seen, at))
    if online and last_seen is not None:
        result.recovered["device_offline"] = last_seen
    elif last_seen is not None:
        explicit_offline = not snapshot.online and fresh(last_seen, at)
        expired = (at - last_seen).total_seconds() >= config.monitoring_offline_seconds
        if explicit_offline or expired:
            reason = "status_expired"
            if not snapshot.mqtt_connected:
                reason = "mqtt_unavailable"
            elif explicit_offline:
                reason = "offline_reported"
            result.opening["device_offline"] = Evidence(
                "device", snapshot.device_id, snapshot.source, last_seen, {"reason": reason}
            )

    if not snapshot.samples:
        return result
    latest = snapshot.samples[0]
    measurement_fresh = latest.valid and fresh(latest.at, at)
    if measurement_fresh:
        result.recovered["sensor_stale"] = latest.at
    elif online and (at - latest.at).total_seconds() >= config.monitoring_stale_seconds:
        result.opening["sensor_stale"] = Evidence(
            "reading", latest.id, latest.source, latest.at, {"reason": "reading_expired"}
        )

    threshold = snapshot.threshold
    if not online or not measurement_fresh or latest.soil is None or threshold is None:
        return result
    if latest.soil < threshold:
        first = _dry_start(snapshot.samples, threshold)
        duration = (latest.at - first).total_seconds()
        if duration >= config.monitoring_dry_seconds:
            result.opening["low_soil"] = Evidence(
                "reading",
                latest.id,
                latest.source,
                latest.at,
                {
                    "soil": latest.soil,
                    "threshold": threshold,
                    "duration_seconds": int(duration),
                    "window_start": first.isoformat(),
                },
            )
    elif latest.soil >= threshold + 3 and len(snapshot.samples) >= 2:
        previous = snapshot.samples[1]
        if previous.soil is not None and previous.soil >= threshold + 3 and fresh(previous.at, at):
            result.recovered["low_soil"] = latest.at
    return result


def read_snapshot(
    db: DatabaseSession, device: Device, at: datetime, mqtt_connected: bool
) -> Snapshot:
    upper = at + timedelta(seconds=5)
    lower = at - timedelta(seconds=settings.monitoring_dry_seconds + 2 * FRESH_SECONDS)
    query = (
        select(Reading)
        .where(Reading.device_id == device.id, Reading.measured_at <= upper)
        .order_by(Reading.measured_at.desc(), Reading.id.desc())
    )
    rows = list(db.scalars(query.where(Reading.measured_at >= lower)))
    if not rows and (last := db.scalar(query.limit(1))):
        rows.append(last)
    samples = {}
    for row in rows:
        if row.measured_at in samples:
            continue
        try:
            values = Readings.model_validate(row.readings)
        except ValidationError:
            samples[row.measured_at] = Sample(row.id, row.source, row.measured_at, None, False)
        else:
            samples[row.measured_at] = Sample(
                row.id, row.source, row.measured_at, values.soil_moisture_pct
            )
    return Snapshot(
        device.id,
        device.source,
        device.last_seen,
        device.online,
        mqtt_connected,
        db.scalar(select(Policy.threshold).where(Policy.id == 1)),
        tuple(samples.values()),
    )


def _create_alert(
    db: DatabaseSession,
    kind: str,
    trigger_type: str,
    device_id: str,
    evidence: Evidence,
    at: datetime,
) -> AgentAlert:
    row = AgentAlert(
        kind=kind,
        trigger_type=trigger_type,
        device_id=device_id,
        severity="info" if kind == "leaf_review" else "warning",
        source_kind=evidence.source_kind,
        source_ref=evidence.source_ref,
        data_source=evidence.data_source,
        evidence=evidence.details,
        observed_at=evidence.observed_at,
        created_at=at,
    )
    db.add(row)
    return row


def sync_conditions(db: DatabaseSession, device_id: str, evaluation: Evaluation, at: datetime):
    base = select(AgentAlert).where(
        AgentAlert.device_id == device_id,
        AgentAlert.trigger_type == "condition",
        AgentAlert.kind.in_(CONDITION_KINDS),
    )
    active = {row.kind: row for row in db.scalars(base.where(AgentAlert.resolved_at.is_(None)))}
    for kind, observed_at in evaluation.recovered.items():
        row = active.get(kind)
        if row is not None and observed_at > row.observed_at:
            row.resolved_at = min(observed_at, at)
    cooldown = timedelta(seconds=settings.monitoring_cooldown_seconds)
    for kind, evidence in evaluation.opening.items():
        if kind in active:
            continue
        last_closed = db.scalar(
            select(func.max(AgentAlert.resolved_at)).where(
                AgentAlert.device_id == device_id,
                AgentAlert.trigger_type == "condition",
                AgentAlert.kind == kind,
            )
        )
        if last_closed is None or (
            at - last_closed >= cooldown and evidence.observed_at >= last_closed
        ):
            _create_alert(db, kind, "condition", device_id, evidence, at)


def _event_time(event: dict) -> datetime | None:
    try:
        value = datetime.fromisoformat(event["at"])
    except (KeyError, TypeError, ValueError):
        return None
    return value if value.tzinfo is not None else None


def command_evidence(command: Command, since: datetime) -> tuple[Evidence, datetime | None] | None:
    failures = []
    completions = []
    for event in command.events or []:
        if not isinstance(event, dict) or (timestamp := _event_time(event)) is None:
            continue
        if event.get("status") in ("failed", "timed_out"):
            failures.append((timestamp, event["status"]))
        elif event.get("status") == "completed":
            completions.append(timestamp)
    if not failures and command.status in ("failed", "timed_out"):
        failures.append((command.updated_at, command.status))
    recent_failures = [(timestamp, status) for timestamp, status in failures if timestamp >= since]
    if not recent_failures:
        return None
    observed_at, status = min(recent_failures)
    if command.status == "completed" and not completions:
        completions.append(command.updated_at)
    completed_at = min((stamp for stamp in completions if stamp >= observed_at), default=None)
    return Evidence(
        "command",
        command.id,
        command.source,
        observed_at,
        {"status": status, "action": command.action},
    ), completed_at


def leaf_evidence(job: Job, image: PlantImage, min_score: float) -> Evidence | None:
    if (
        job.status != "completed"
        or job.provider != "trained"
        or job.task != "leaf"
        or image.purpose != "leaf"
        or job.finished_at is None
        or not isinstance(job.result, dict)
        or job.result.get("is_mock") is not False
    ):
        return None
    label, score = job.result.get("class_name"), job.result.get("confidence")
    if (
        not isinstance(label, str)
        or label not in LEAF_LABELS
        or label == "Tomato___healthy"
        or type(score) not in (float, int)
        or not min_score <= score <= 1
    ):
        return None
    return Evidence(
        "image",
        image.id,
        image.source,
        job.finished_at,
        {
            "class_name": label,
            "label": LEAF_LABELS[label],
            "score": score,
            "model_version": job.model_version,
            "job_id": job.id,
            "image_created_at": image.created_at.isoformat(),
        },
    )


def _event_alert(
    db: DatabaseSession, device_id: str, kind: str, source_ref: str
) -> AgentAlert | None:
    return db.scalar(
        select(AgentAlert).where(
            AgentAlert.device_id == device_id,
            AgentAlert.kind == kind,
            AgentAlert.trigger_type == "event",
            AgentAlert.source_ref == source_ref,
        )
    )


def _sync_command(db: DatabaseSession, command: Command, since: datetime, at: datetime):
    issue = command_evidence(command, since)
    if issue is None:
        return
    evidence, completed_at = issue
    alert = _event_alert(db, command.device_id, "irrigation_error", command.id)
    if alert is None:
        alert = _create_alert(db, "irrigation_error", "event", command.device_id, evidence, at)
    if completed_at is not None and alert.resolved_at is None:
        alert.resolved_at = completed_at


def _sync_leaf(db: DatabaseSession, job: Job, image: PlantImage, at: datetime):
    evidence = leaf_evidence(job, image, settings.monitoring_leaf_score)
    if evidence is None or _event_alert(db, image.device_id, "leaf_review", image.id):
        return
    last = db.scalar(
        select(func.max(AgentAlert.observed_at)).where(
            AgentAlert.device_id == image.device_id,
            AgentAlert.kind == "leaf_review",
            AgentAlert.evidence["class_name"].as_string() == evidence.details["class_name"],
        )
    )
    wait = timedelta(minutes=settings.monitoring_leaf_cooldown_minutes)
    if last is None or evidence.observed_at - last >= wait:
        _create_alert(db, "leaf_review", "event", image.device_id, evidence, at)


def _scan(db, query, timestamp, row_id, started_at, cursor, at):
    position = tuple_(timestamp, row_id)
    query = query.where(timestamp >= started_at, timestamp <= at)
    overlap = db.execute(
        query.where(timestamp >= cursor[0] - EVENT_OVERLAP, position <= cursor)
        .order_by(timestamp.desc(), row_id.desc())
        .limit(EVENT_BATCH)
    ).all()
    forward = db.execute(
        query.where(position > cursor).order_by(timestamp, row_id).limit(EVENT_BATCH)
    ).all()
    next_cursor = tuple(forward[-1][-2:]) if len(forward) == EVENT_BATCH else (at, "")
    return [*reversed(overlap), *forward], max(cursor, next_cursor)


def _checkpoint(db: DatabaseSession, device_id: str, at: datetime) -> MonitoringCheckpoint:
    db.execute(
        insert(MonitoringCheckpoint)
        .values(
            device_id=device_id,
            started_at=at,
            command_at=at,
            command_id="",
            job_at=at,
            job_id="",
        )
        .on_conflict_do_nothing(index_elements=[MonitoringCheckpoint.device_id])
    )
    return db.get(MonitoringCheckpoint, device_id, with_for_update=True)


def evaluate_monitoring(mqtt_connected: bool, at: datetime, session_factory=Session):
    with session_factory.begin() as db:
        device = db.get(Device, DEVICE_ID)
        if device is None:
            return
        checkpoint = _checkpoint(db, device.id, at)
        db.refresh(device)
        at = max(at, checkpoint.command_at, checkpoint.job_at)
        snapshot = read_snapshot(db, device, at, mqtt_connected)
        sync_conditions(db, device.id, evaluate_conditions(snapshot, at), at)

        commands, (checkpoint.command_at, checkpoint.command_id) = _scan(
            db,
            select(Command, Command.updated_at, Command.id).where(Command.device_id == device.id),
            Command.updated_at,
            Command.id,
            checkpoint.started_at,
            (checkpoint.command_at, checkpoint.command_id),
            at,
        )
        for command, _, _ in commands:
            _sync_command(db, command, checkpoint.started_at, at)

        jobs, (checkpoint.job_at, checkpoint.job_id) = _scan(
            db,
            select(Job, PlantImage, Job.finished_at, Job.id)
            .join(PlantImage, Job.image_id == PlantImage.id)
            .where(
                PlantImage.device_id == device.id, Job.task == "leaf", Job.status == "completed"
            ),
            Job.finished_at,
            Job.id,
            checkpoint.started_at,
            (checkpoint.job_at, checkpoint.job_id),
            at,
        )
        for job, image, _, _ in jobs:
            _sync_leaf(db, job, image, at)
