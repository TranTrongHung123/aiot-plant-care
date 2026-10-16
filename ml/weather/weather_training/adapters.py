"""Chuẩn hóa snapshot OpenWeather, tách measured_at và received_at."""

import numpy as np
import pandas as pd

from .data import BASE_COLUMNS, utc_timestamp


def openweather_snapshot(payload, *, received_at, units):
    if units != "metric":
        raise ValueError("OpenWeather phải được gọi với units=metric (°C, m/s)")
    if str(payload.get("cod", 200)) != "200":
        raise ValueError("OpenWeather trả lỗi")
    main = payload["main"]
    if "grnd_level" not in main:
        raise ValueError("Thiếu áp suất mặt đất grnd_level; không thay bằng pressure mực biển")
    measured = pd.Timestamp(payload["dt"], unit="s", tz="UTC")
    received = utc_timestamp(received_at)
    if measured > received:
        raise ValueError("Snapshot có thời điểm đo sau thời điểm nhận")
    values = [main["temp"], main["humidity"], main["grnd_level"], payload["clouds"]["all"], payload["wind"]["speed"]]
    values = [float(value) for value in values]
    if not np.isfinite(values).all():
        raise ValueError("Snapshot thiếu giá trị hữu hạn")
    return {"source": "openweather_current", "measured_at": measured.isoformat(),
            "received_at": received.isoformat(), **dict(zip(BASE_COLUMNS, values))}


def snapshots_to_hourly(snapshots, *, as_of, max_age_minutes=15):
    """Tại mỗi giờ chỉ dùng snapshot đã nhận trước giờ đó; tối đa cũ 15 phút."""
    if not 0 < max_age_minutes <= 15:
        raise ValueError("max_age_minutes phải thuộc (0,15]")
    reference = utc_timestamp(as_of)
    if reference != reference.floor("h"):
        raise ValueError("as_of phải căn đúng giờ")
    parsed = []
    for snapshot in snapshots:
        if snapshot["source"] != "openweather_current":
            raise ValueError("Không trộn nguồn trong lịch sử inference")
        measured, received = utc_timestamp(snapshot["measured_at"]), utc_timestamp(snapshot["received_at"])
        if measured > received:
            raise ValueError("Snapshot sai thứ tự measured_at/received_at")
        parsed.append((measured, received, snapshot))
    rows = []
    for timestamp in pd.date_range(reference - pd.Timedelta(hours=5), reference, freq="h"):
        candidates = [(measured, received, snapshot) for measured, received, snapshot in parsed
                      if received <= timestamp and pd.Timedelta(0) <= timestamp - measured <= pd.Timedelta(minutes=max_age_minutes)]
        if not candidates:
            raise ValueError(f"Thiếu snapshot đã sẵn có và còn mới cho giờ {timestamp}")
        measured, received, snapshot = max(candidates, key=lambda item: (item[0], item[1]))
        rows.append({"time": timestamp, "available_at": received, "measured_at": measured,
                     **{name: snapshot[name] for name in BASE_COLUMNS}})
    return pd.DataFrame(rows).set_index("time")
