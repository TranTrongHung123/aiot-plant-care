import io
import logging
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import inspect, select
from starlette.concurrency import run_in_threadpool

from app.core.schemas import (
    DEVICE_ID,
    PolicyRequest,
    PumpRequest,
    ScenarioRequest,
    WeatherModeRequest,
    fresh,
)
from app.core.settings import settings
from app.db.models import Command, Device, Job, PlantImage, Policy, Reading, WeatherControl, now
from app.db.session import Session
from app.integrations.mqtt import bridge
from app.integrations.storage import normalize_image, storage
from app.integrations.weather import weather_inputs
from app.modules.control import create_command
from app.modules.trained_ai import InferenceUnavailable, model_metadata, model_status
from app.modules.weather_control import active_weather, enqueue_live_forecast

log = logging.getLogger(__name__)


def valid_token(token):
    import secrets

    return isinstance(token, str) and secrets.compare_digest(token, settings.app_token)


def authorize(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(HTTPBearer(auto_error=False))
    ],
):
    if not credentials or not valid_token(credentials.credentials):
        raise HTTPException(401, "Khóa truy cập không hợp lệ")


router = APIRouter(prefix="/api/v1", dependencies=[Depends(authorize)])


def serialize(item):
    return jsonable_encoder(
        {column.key: getattr(item, column.key) for column in inspect(item).mapper.column_attrs}
    )


def dashboard():
    at = now()
    with Session() as db:
        device = db.get(Device, DEVICE_ID)
        history = list(db.scalars(select(Reading).order_by(Reading.measured_at.desc()).limit(60)))
        online = bool(bridge.connected and device.online and fresh(device.last_seen, at))
        try:
            model_metadata("weather")
            weather_inputs(db, at)
            weather_status = {
                "available": True,
                "reason": "Đủ lịch sử OpenWeather; thử nghiệm sai lệch nguồn",
            }
        except InferenceUnavailable as exc:
            weather_status = {"available": False, "reason": str(exc)}
        return {
            "source": "simulation",
            "ai_provider": "trained",
            "models": model_status(),
            "weather_status": weather_status,
            "weather_control": active_weather(db, at),
            "server_time": at.isoformat(),
            "mqtt_connected": bridge.connected,
            "device": {
                **serialize(device),
                "online": online,
                "pump": device.pump if online else "unknown",
            },
            "latest": serialize(history[0]) if history else None,
            "stale": not history or not fresh(history[0].measured_at, at),
            "history": [serialize(row) for row in reversed(history)],
            "commands": [
                serialize(row)
                for row in db.scalars(select(Command).order_by(Command.issued_at.desc()).limit(30))
            ],
            "jobs": [
                serialize(row)
                for row in db.scalars(select(Job).order_by(Job.created_at.desc()).limit(30))
            ],
            "policy": serialize(db.get(Policy, 1)),
        }


@router.get("/dashboard")
def get_dashboard():
    return dashboard()


@router.post("/irrigation/commands", status_code=202)
def pump(request: PumpRequest):
    return serialize(create_command(request, bridge))


@router.put("/irrigation/policy")
def policy(request: PolicyRequest):
    with Session.begin() as db:
        db.scalar(select(Device).where(Device.id == DEVICE_ID).with_for_update())
        item = db.scalar(select(Policy).where(Policy.id == 1).with_for_update())
        if any(getattr(item, key) != value for key, value in request.model_dump().items()):
            item.schedule_started_at = now()
            item.last_schedule_at, item.last_schedule_reason = None, ""
            item.last_schedule_status = ""
        for key, value in request.model_dump().items():
            setattr(item, key, value)
        return serialize(item)


@router.put("/weather/mode")
def set_weather_mode(request: WeatherModeRequest):
    if request.mode == "demo" and settings.device_mode != "simulation":
        raise HTTPException(409, "DEMO chỉ hỗ trợ thiết bị simulation")
    with Session.begin() as db:
        db.scalar(select(Device).where(Device.id == DEVICE_ID).with_for_update())
        control = db.get(WeatherControl, 1)
        if control.mode != request.mode or control.demo_rain != request.demo_rain:
            control.mode, control.demo_rain = request.mode, request.demo_rain
            control.updated_at = now()
        db.flush()
        return active_weather(db)


@router.post("/simulation/scenario", status_code=202)
def scenario(request: ScenarioRequest):
    try:
        bridge.publish("simulation-config", request.model_dump())
    except RuntimeError:
        raise HTTPException(503, "MQTT chưa sẵn sàng") from None
    return {"requested": request.scenario, "confirmed": False}


@router.post("/ai/weather", status_code=202)
def weather():
    with Session.begin() as db:
        try:
            job = enqueue_live_forecast(db)
        except InferenceUnavailable as exc:
            raise HTTPException(409, str(exc)) from None
        return serialize(job)


def store_image(data, purpose):
    try:
        data, width, height = normalize_image(data)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    try:
        metadata = model_metadata(purpose)
    except InferenceUnavailable as exc:
        raise HTTPException(503, str(exc)) from None
    image_id = str(uuid4())
    key = f"simulation/{DEVICE_ID}/{now():%Y/%m}/{image_id}.jpg"
    try:
        storage.put_object(
            settings.minio_images_bucket,
            key,
            io.BytesIO(data),
            len(data),
            content_type="image/jpeg",
        )
    except Exception:
        log.exception("Image storage failed")
        raise HTTPException(503, "MinIO chưa sẵn sàng; vui lòng thử lại") from None
    try:
        with Session.begin() as db:
            image = PlantImage(
                id=image_id,
                device_id=DEVICE_ID,
                purpose=purpose,
                object_key=key,
                content_type="image/jpeg",
                width=width,
                height=height,
            )
            db.add(image)
            db.flush()
            job = Job(
                task=purpose,
                image_id=image.id,
                input={},
                provider="trained",
                model_version=metadata["model_version"],
            )
            db.add(job)
            db.flush()
            return serialize(job)
    except Exception:
        try:
            storage.remove_object(settings.minio_images_bucket, key)
        except Exception:
            log.exception("Orphan object requires cleanup: %s", key)
        raise


@router.post("/images", status_code=202)
async def upload(
    purpose: Annotated[str, Query(pattern="^(leaf|fruit)$")],
    file: Annotated[UploadFile, File()],
):
    try:
        data = await file.read(5 * 1024 * 1024 + 1)
    finally:
        await file.close()
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(413, "Ảnh tối đa 5 MiB")
    return await run_in_threadpool(store_image, data, purpose)


@router.get("/images/{image_id}/content")
def image_content(image_id: str):
    with Session() as db:
        image = db.get(PlantImage, image_id)
        if not image:
            raise HTTPException(404, "Không tìm thấy ảnh")
    try:
        result = storage.get_object(settings.minio_images_bucket, image.object_key)
        try:
            return Response(
                result.read(),
                media_type="image/jpeg",
                headers={"Cache-Control": "private, max-age=300"},
            )
        finally:
            result.close()
            result.release_conn()
    except Exception:
        raise HTTPException(503, "Không đọc được ảnh từ MinIO") from None


@router.get("/jobs/{job_id}")
def get_job(job_id: str):
    with Session() as db:
        item = db.get(Job, job_id)
        if not item:
            raise HTTPException(404, "Không tìm thấy tác vụ")
        return serialize(item)


@router.post("/jobs/{job_id}/retry", status_code=202)
def retry_job(job_id: str):
    with Session.begin() as db:
        item = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if not item:
            raise HTTPException(404, "Không tìm thấy tác vụ")
        if item.status != "failed":
            raise HTTPException(409, "Chỉ thử lại tác vụ đã thất bại")
        if item.provider != "trained":
            raise HTTPException(409, "Job Mock cũ đã ngừng hỗ trợ; hãy gửi yêu cầu mới")
        item.status, item.error, item.attempts = "queued", None, 0
        item.lease_until, item.finished_at = None, None
        return serialize(item)
