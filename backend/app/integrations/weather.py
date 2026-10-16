"""Thu thập OpenWeather trong worker; không biến dữ liệu forecast thành quan trắc."""

import logging
import math
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select

from app.core.settings import settings
from app.db.models import WeatherObservation, now
from app.db.session import Session
from app.modules.trained_ai import InferenceUnavailable

log = logging.getLogger(__name__)
# HTTPX ghi URL ở INFO, trong khi OpenWeather yêu cầu key ở query string.
logging.getLogger("httpx").setLevel(logging.WARNING)


def weather_site():
    if (
        not settings.openweather_api_key
        or not settings.weather_latitude
        or not settings.weather_longitude
    ):
        raise InferenceUnavailable(
            "Cần cấu hình OPENWEATHER_API_KEY, WEATHER_LATITUDE và WEATHER_LONGITUDE trong .env"
        )
    try:
        lat, lon = float(settings.weather_latitude), float(settings.weather_longitude)
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError()
    except ValueError:
        raise InferenceUnavailable("Tọa độ OpenWeather không hợp lệ") from None
    return f"{lat:.6f},{lon:.6f}"


def normalize_snapshot(payload, received):
    main = payload["main"]
    if str(payload.get("cod", 200)) != "200" or "grnd_level" not in main:
        raise ValueError("OpenWeather thiếu áp suất mặt đất hoặc trả lỗi")
    values = {
        "temperature_c": main["temp"],
        "humidity_pct": main["humidity"],
        "surface_pressure_hpa": main["grnd_level"],
        "cloud_cover_pct": payload["clouds"]["all"],
        "wind_speed_ms": payload["wind"]["speed"],
    }
    limits = [(-90, 65), (0, 100), (300, 1100), (0, 100), (0, 150)]
    values = {key: float(value) for key, value in values.items()}
    if any(
        not math.isfinite(value) or not low <= value <= high
        for value, (low, high) in zip(values.values(), limits, strict=True)
    ):
        raise ValueError("OpenWeather trả giá trị ngoài phạm vi")
    measured = datetime.fromtimestamp(payload["dt"], UTC)
    if not timedelta(0) <= received - measured <= timedelta(minutes=15):
        raise ValueError("Quan trắc OpenWeather quá cũ hoặc nằm trong tương lai")
    return measured, values


def collect_weather():
    try:
        site = weather_site()
    except InferenceUnavailable:
        return False
    try:
        # Không log URL/exception HTTP: query string chứa API key.
        response = httpx.get(
            "https://api.openweathermap.org/data/2.5/weather",
            params={
                "lat": settings.weather_latitude,
                "lon": settings.weather_longitude,
                "appid": settings.openweather_api_key,
                "units": "metric",
            },
            timeout=10,
        )
        if response.status_code != 200:
            log.warning("OpenWeather HTTP %s", response.status_code)
            return False
        received = now()
        measured, readings = normalize_snapshot(response.json(), received)
        with Session.begin() as db:
            db.add(
                WeatherObservation(
                    site=site, measured_at=measured, received_at=received, readings=readings
                )
            )
        return True
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        log.warning("Không thu thập được quan trắc OpenWeather hợp lệ")
        return False


def weather_inputs(db, at=None):
    site = weather_site()
    if not settings.weather_allow_domain_shift:
        raise InferenceUnavailable("WEATHER_ALLOW_DOMAIN_SHIFT chưa bật cho thử nghiệm OpenWeather")
    at = at or now()
    reference = at.replace(minute=0, second=0, microsecond=0)
    if at - reference > timedelta(minutes=15):
        raise InferenceUnavailable(
            "Model dự báo theo mốc giờ; hãy chạy trong 15 phút đầu giờ kế tiếp"
        )
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
    snapshots = []
    for hours in range(5, -1, -1):
        timestamp = reference - timedelta(hours=hours)
        candidates = [
            row
            for row in records
            if row.measured_at <= row.received_at <= timestamp
            and timestamp - row.measured_at <= timedelta(minutes=15)
        ]
        if not candidates:
            raise InferenceUnavailable(
                "Chưa đủ 6 mốc giờ OpenWeather hợp lệ; worker lấy mẫu mỗi 60 giây. "
                "Cần quan trắc đo/nhận trước từng mốc giờ, không quá cũ 15 phút."
            )
        row = max(candidates, key=lambda row: (row.measured_at, row.received_at))
        snapshots.append(
            {
                **row.readings,
                "source": row.source,
                "measured_at": row.measured_at.isoformat(),
                "received_at": row.received_at.isoformat(),
            }
        )
    return {
        "snapshots": snapshots,
        "as_of": reference.isoformat(),
        "weather_source": "openweather_current",
        "site": site,
    }
