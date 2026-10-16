"""Nguồn thời tiết dùng cho quy tắc tưới: kịch bản DEMO hoặc dự báo LIVE."""

import math
from datetime import datetime, timedelta

from sqlalchemy import select

from app.core.schemas import DEVICE_ID
from app.core.settings import settings
from app.db.models import Device, Job, WeatherControl, now
from app.integrations.weather import weather_inputs, weather_site
from app.modules.trained_ai import InferenceUnavailable, model_metadata


def live_result(job, at, *, site, version):
    """Chặn kết quả cũ, replay, sai địa điểm/version và kết quả lỗi định dạng."""
    if not job or job.provider != "trained" or job.source != "openweather_current":
        return None
    result = job.result or {}
    try:
        start = datetime.fromisoformat(result["forecast_start"])
        end = datetime.fromisoformat(result["forecast_end"])
        probability, threshold = float(result["rain_probability"]), float(result["threshold"])
        if not (
            job.status == "completed"
            and job.model_version == version == result["model_version"]
            and job.input.get("site") == site
            and result.get("source") == "openweather_current"
            and result.get("is_mock") is False
            and result.get("historical_replay") is False
            and start <= at < end
            and end - start == timedelta(hours=1)
            and math.isfinite(probability)
            and 0 <= probability <= 1
            and math.isfinite(threshold)
            and 0 < threshold < 1
            and result["prediction"] in (0, 1)
            and result["prediction"] == int(probability >= threshold)
        ):
            return None
        return result
    except (KeyError, ValueError, TypeError):
        return None


def active_weather(db, at=None):
    at = at or now()
    control = db.get(WeatherControl, 1)
    state = {
        "mode": control.mode,
        "demo_rain": control.demo_rain,
        "updated_at": control.updated_at.isoformat(),
        "available": False,
        "rain_expected": None,
        "rain_probability": None,
        "model_version": None,
        "job_id": None,
        "forecast_start": None,
        "forecast_end": None,
        "source": "demo_scenario" if control.mode == "demo" else "openweather_current",
    }
    if control.mode == "demo":
        if settings.device_mode != "simulation":
            return {**state, "reason": "DEMO không được phép điều khiển thiết bị live"}
        return {
            **state,
            "available": True,
            "rain_expected": control.demo_rain,
            "reason": "DEMO có mưa: chặn tưới tự động"
            if control.demo_rain
            else "DEMO không mưa: có thể tưới theo lịch khi đất khô và đủ điều kiện an toàn",
        }
    try:
        site = weather_site()
        metadata = model_metadata("weather")
        if not settings.weather_allow_domain_shift:
            raise InferenceUnavailable("Chưa cho phép thử nghiệm nguồn OpenWeather")
    except InferenceUnavailable as exc:
        return {**state, "reason": f"LIVE chưa khả dụng: {exc}"}
    job = db.scalar(
        select(Job)
        .where(
            Job.task == "weather",
            Job.provider == "trained",
            Job.source == "openweather_current",
            Job.status == "completed",
            Job.model_version == metadata["model_version"],
            Job.input["site"].as_string() == site,
            Job.created_at >= at - timedelta(hours=1),
        )
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    result = live_result(job, at, site=site, version=metadata["model_version"])
    if result is None:
        return {**state, "reason": "LIVE chưa có dự báo hợp lệ còn hạn; tạm chặn tưới tự động"}
    rain = bool(result["prediction"])
    return {
        **state,
        "available": True,
        "rain_expected": rain,
        "rain_probability": result["rain_probability"],
        "model_version": job.model_version,
        "job_id": job.id,
        "forecast_start": result["forecast_start"],
        "forecast_end": result["forecast_end"],
        "reason": "LIVE dự báo có mưa: chặn tưới tự động"
        if rain
        else "LIVE dự báo ít mưa: có thể tưới theo lịch khi đất khô và đủ điều kiện an toàn",
    }


def enqueue_live_forecast(db, at=None):
    """Một job/mốc giờ/địa điểm/version; cùng khóa với đổi mode và lệnh tưới."""
    at = at or now()
    db.scalar(select(Device).where(Device.id == DEVICE_ID).with_for_update())
    if db.get(WeatherControl, 1).mode != "live":
        raise InferenceUnavailable("Đang chọn DEMO; chuyển LIVE để chạy dự báo XGBoost")
    metadata = model_metadata("weather")
    inputs = weather_inputs(db, at)
    existing = db.scalar(
        select(Job)
        .where(
            Job.task == "weather",
            Job.provider == "trained",
            Job.source == "openweather_current",
            Job.model_version == metadata["model_version"],
            Job.input["site"].as_string() == inputs["site"],
            Job.input["as_of"].as_string() == inputs["as_of"],
        )
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    if existing:
        return existing
    job = Job(
        task="weather",
        source="openweather_current",
        provider="trained",
        model_version=metadata["model_version"],
        input=inputs,
    )
    db.add(job)
    db.flush()
    return job
