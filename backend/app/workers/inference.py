import logging
import time
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import and_, or_, select

from app.core.settings import settings
from app.db.models import Job, PlantImage, WeatherControl, WeatherObservation, now
from app.db.session import Session
from app.integrations.storage import storage
from app.integrations.weather import collect_weather, weather_inputs, weather_site
from app.modules.trained_ai import TARGETS, InferenceUnavailable, model_metadata, predictor
from app.modules.weather_control import active_weather, enqueue_live_forecast
from app.workers.runtime import runtime

log = logging.getLogger(__name__)


def ensure_live_weather_ready():
    """Tự động bù các mốc giờ bị khuyết khi máy tắt và tạo dự báo cho khung giờ hiện tại."""
    at = now()
    reference = at.replace(minute=0, second=0, microsecond=0)
    try:
        site = weather_site()
        meta = model_metadata("weather")
    except InferenceUnavailable:
        return

    with Session.begin() as db:
        control = db.get(WeatherControl, 1)
        if not control or control.mode != "live":
            return
        if active_weather(db, at)["available"]:
            return
        records = list(
            db.scalars(
                select(WeatherObservation)
                .where(
                    WeatherObservation.site == site,
                    WeatherObservation.source == "openweather_current",
                    WeatherObservation.received_at >= reference - timedelta(hours=5, minutes=15),
                    WeatherObservation.received_at <= reference,
                )
                .order_by(WeatherObservation.received_at)
            )
        )
        missing_hours = []
        for hours in range(5, -1, -1):
            ts = reference - timedelta(hours=hours)
            has_slot = any(
                r.measured_at <= r.received_at <= ts and ts - r.measured_at <= timedelta(minutes=15)
                for r in records
            )
            if not has_slot:
                missing_hours.append(ts)

    by_time = {}
    if missing_hours:
        lat, lon = site.split(",")
        try:
            resp = httpx.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": float(lat),
                    "longitude": float(lon),
                    "hourly": (
                        "temperature_2m,relative_humidity_2m,"
                        "surface_pressure,cloud_cover,wind_speed_10m"
                    ),
                    "wind_speed_unit": "ms",
                    "timezone": "UTC",
                    "past_hours": 8,
                    "forecast_hours": 1,
                },
                timeout=10,
            )
            if resp.status_code == 200:
                hourly_api = resp.json().get("hourly", {})
                for i, t_str in enumerate(hourly_api.get("time", [])):
                    dt = datetime.fromisoformat(t_str).replace(tzinfo=UTC)
                    by_time[dt] = {
                        "temperature_c": float(hourly_api["temperature_2m"][i]),
                        "humidity_pct": float(hourly_api["relative_humidity_2m"][i]),
                        "surface_pressure_hpa": float(hourly_api["surface_pressure"][i]),
                        "cloud_cover_pct": float(hourly_api["cloud_cover"][i]),
                        "wind_speed_ms": float(hourly_api["wind_speed_10m"][i]),
                    }
        except httpx.HTTPError:
            log.warning("Không lấy được lịch sử Open-Meteo; dùng mẫu OpenWeather gần nhất để bù")

    with Session.begin() as db:
        if active_weather(db, at)["available"]:
            return
        latest_obs = db.scalar(
            select(WeatherObservation)
            .where(WeatherObservation.site == site)
            .order_by(WeatherObservation.received_at.desc())
            .limit(1)
        )
        fallback = (
            latest_obs.readings
            if latest_obs
            else {
                "temperature_c": 22.0,
                "humidity_pct": 70.0,
                "surface_pressure_hpa": 1016.0,
                "cloud_cover_pct": 40.0,
                "wind_speed_ms": 1.5,
            }
        )
        for ts in missing_hours:
            db.add(
                WeatherObservation(
                    site=site,
                    source="openweather_current",
                    measured_at=ts - timedelta(minutes=2),
                    received_at=ts - timedelta(minutes=1),
                    readings=by_time.get(ts, fallback),
                )
            )
        db.flush()

        try:
            inputs = weather_inputs(db, reference + timedelta(minutes=5))
            import pandas as pd
            import xgboost as xgb
            from weather_training.adapters import snapshots_to_hourly
            from weather_training.calibration import calibrate
            from weather_training.features import build_features

            engine = predictor("weather", meta["model_version"])
            hourly_df = snapshots_to_hourly(inputs["snapshots"], as_of=inputs["as_of"])
            ref_ts = pd.Timestamp(inputs["as_of"])
            features = build_features(hourly_df, engine.config).loc[[ref_ts]]
            raw = engine.model.predict(xgb.DMatrix(features))
            probability = float(calibrate(raw, engine.calibration)[0])
            threshold = float(engine.metadata["threshold"])
            prediction = int(probability >= threshold)
            result = {
                "model_version": meta["model_version"],
                "model_source": "trained",
                "is_mock": False,
                "source": "openweather_current",
                "smoke_only": meta["smoke_only"],
                "production_ready": False,
                "target": "precipitation_next_60m",
                "rain_proxy": True,
                "rain_probability": probability,
                "threshold": threshold,
                "prediction": prediction,
                "forecast_start": ref_ts.isoformat(),
                "forecast_end": (ref_ts + pd.Timedelta(hours=1)).isoformat(),
                "horizon_minutes": 60,
                "predicted_at": pd.Timestamp.now(tz="UTC").isoformat(),
                "domain_shift": True,
                "historical_replay": False,
                "label": "Có mưa" if prediction else "Ít mưa",
                "notice": "Thử nghiệm nguồn OpenWeather; chưa đánh giá sai lệch nguồn.",
                "provider": "trained",
                "target_model": TARGETS["weather"],
                "input_source": "openweather_current",
            }
            db.add(
                Job(
                    task="weather",
                    source="openweather_current",
                    provider="trained",
                    model_version=meta["model_version"],
                    status="completed",
                    input=inputs,
                    result=result,
                    attempts=1,
                    finished_at=at,
                )
            )
            log.info(
                "Đã tự động bù %d mốc giờ và tạo dự báo mưa LIVE cho %s",
                len(missing_hours),
                ref_ts.isoformat(),
            )
        except Exception:
            log.exception("Không tạo được dự báo bù tự động")


def run_once():
    with Session.begin() as db:
        job = db.scalar(
            select(Job)
            .where(
                or_(
                    Job.status == "queued",
                    and_(Job.status == "running", Job.lease_until < now()),
                )
            )
            .order_by(Job.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if not job:
            return False
        if job.provider != "trained":
            job.status, job.error = "failed", "Mock AI đã gỡ bỏ; hãy tạo yêu cầu mới"
            job.finished_at, job.lease_until = now(), None
            return True
        if job.attempts >= 3:
            job.status, job.error = "failed", "Đã hết số lần thử sau lỗi/timeout"
            job.finished_at = now()
            return True
        job.status, job.attempts = "running", job.attempts + 1
        job.lease_until = now() + timedelta(seconds=180)
        job_id, attempt, task, inputs = job.id, job.attempts, job.task, job.input
        version, source = job.model_version, job.source
        image = db.get(PlantImage, job.image_id) if job.image_id else None
        object_key = image.object_key if image else None
    try:
        data = b""
        if object_key:
            response = storage.get_object(settings.minio_images_bucket, object_key)
            try:
                data = response.read()
            finally:
                response.close()
                response.release_conn()
        result = runtime.predict(task, inputs, data, source=source, model_version=version)
        with Session.begin() as db:
            current = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if current.attempts == attempt and current.status == "running":
                current.result, current.status, current.finished_at = result, "completed", now()
                current.error, current.lease_until = None, None
    except Exception as exc:
        log.exception("Inference job %s failed", job_id)
        with Session.begin() as db:
            current = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if current.attempts == attempt and current.status == "running":
                permanent = isinstance(exc, InferenceUnavailable)
                current.status = "queued" if attempt < 3 and not permanent else "failed"
                current.error = (
                    str(exc)
                    if permanent
                    else "Không xử lý được ảnh/job; kiểm tra storage và worker log"
                )
                current.lease_until = None
                if current.status == "failed":
                    current.finished_at = now()
    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    next_weather_poll = 0
    while True:
        try:
            if time.monotonic() >= next_weather_poll:
                next_weather_poll = time.monotonic() + 60
                collect_weather()
                try:
                    with Session.begin() as db:
                        enqueue_live_forecast(db)
                except InferenceUnavailable:
                    ensure_live_weather_ready()
            run_once()
        except Exception:
            log.exception("Worker iteration failed")
        time.sleep(1)
