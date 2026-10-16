"""Đọc Open-Meteo, chuẩn hóa UTC/đơn vị; không nội suy dữ liệu tương lai."""

import csv
import hashlib
import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

BASE_COLUMNS = ["temperature_c", "humidity_pct", "surface_pressure_hpa", "cloud_cover_pct", "wind_speed_ms"]
CSV_COLUMNS = {
    "temperature_2m (°C)": "temperature_c",
    "relative_humidity_2m (%)": "humidity_pct",
    "surface_pressure (hPa)": "surface_pressure_hpa",
    "cloud_cover (%)": "cloud_cover_pct",
    "wind_speed_10m (km/h)": "wind_speed_ms",
    "precipitation (mm)": "precipitation_mm",
}
UNITS = {"temperature_c": "degC", "humidity_pct": "%", "surface_pressure_hpa": "hPa",
         "cloud_cover_pct": "%", "wind_speed_ms": "m/s", "precipitation_mm": "mm/preceding_hour"}


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def utc_timestamp(value):
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        raise ValueError("Thời điểm phải có timezone")
    return timestamp.tz_convert("UTC")


def validate_hourly(frame, *, require_target=True):
    required = BASE_COLUMNS + (["precipitation_mm"] if require_target else [])
    if not set(required).issubset(frame.columns):
        raise ValueError(f"Thiếu cột: {sorted(set(required) - set(frame.columns))}")
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None:
        raise ValueError("Index thời gian phải có timezone")
    frame = frame[required].copy()
    frame.index = frame.index.tz_convert("UTC")
    frame.index.name = "time"
    if frame.empty or frame.index.hasnans:
        raise ValueError("Dữ liệu rỗng hoặc thời gian không hợp lệ")
    if not (frame.index == frame.index.floor("h")).all():
        raise ValueError("Dữ liệu phải căn đúng giờ; không tự làm tròn timestamp")
    for column in required:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    duplicates = int(frame.index.duplicated().sum())
    if duplicates:
        for _, group in frame[frame.index.duplicated(keep=False)].groupby(level=0):
            if (group.nunique(dropna=False) > 1).any():
                raise ValueError("Timestamp trùng nhưng giá trị mâu thuẫn")
        frame = frame[~frame.index.duplicated(keep="first")]
    frame = frame.sort_index()
    if frame.index.max() - frame.index.min() > pd.Timedelta(days=365 * 30):
        raise ValueError("Phạm vi thời gian quá lớn cho pipeline này")
    ranges = {"temperature_c": (-90, 65), "humidity_pct": (0, 100),
              "surface_pressure_hpa": (300, 1100), "cloud_cover_pct": (0, 100),
              "wind_speed_ms": (0, 150), "precipitation_mm": (0, 1000)}
    invalid = {}
    for column in required:
        low, high = ranges[column]
        bad = frame[column].notna() & (~np.isfinite(frame[column]) | ~frame[column].between(low, high))
        invalid[column] = int(bad.sum())
        frame.loc[bad, column] = np.nan
    original_rows = len(frame)
    frame = frame.reindex(pd.date_range(frame.index.min(), frame.index.max(), freq="h", name="time"))
    audit = {"rows": len(frame), "duplicate_rows_removed": duplicates,
             "missing_hours": len(frame) - original_rows, "invalid_values": invalid,
             "missing_values": {c: int(frame[c].isna().sum()) for c in required},
             "start_utc": frame.index.min().isoformat(), "end_utc": frame.index.max().isoformat()}
    return frame, audit


def load_open_meteo_csv(path):
    path = Path(path)
    raw = path.read_bytes()
    lines = raw.decode("utf-8-sig").splitlines()
    header = next((i for i, line in enumerate(lines) if line.startswith("time,")), None)
    if header is None or header < 2:
        raise ValueError("Cần CSV Open-Meteo có metadata timezone và header time")
    metadata_rows = list(csv.DictReader(io.StringIO("\n".join(lines[:header]).strip())))
    if len(metadata_rows) != 1:
        raise ValueError("Chỉ hỗ trợ một địa điểm trong mỗi CSV")
    metadata = metadata_rows[0]
    offset = int(metadata["utc_offset_seconds"])
    table = pd.read_csv(io.StringIO("\n".join(lines[header:])), na_values=["NaN", "nan", "null"])
    if not set(CSV_COLUMNS).issubset(table.columns):
        raise ValueError("CSV thiếu cột hoặc đơn vị không đúng (°C, %, hPa, km/h, mm)")
    index = pd.DatetimeIndex(pd.to_datetime(table.pop("time"), errors="raise"))
    if index.tz is not None:
        raise ValueError("CSV export yêu cầu giờ địa phương không offset; offset nằm ở metadata")
    table = table.rename(columns=CSV_COLUMNS)
    table.index = index.tz_localize(timezone(timedelta(seconds=offset))).tz_convert("UTC")
    table["wind_speed_ms"] = pd.to_numeric(table["wind_speed_ms"], errors="raise") / 3.6
    frame, audit = validate_hourly(table)
    return frame, {"provider": "open_meteo_archive", "source_kind": "historical_reconstruction",
                   "csv_metadata": metadata, "sha256": hashlib.sha256(raw).hexdigest(),
                   "units": UNITS, "audit": audit,
                   "availability": "Không có thời điểm từng giá trị thực sự sẵn có trong quá khứ."}


def download_open_meteo(destination, *, latitude, longitude, start_date, end_date, model="ecmwf_ifs"):
    """Tải từng năm, lưu response thô và trả bảng chuẩn hóa; không tự đổi model nguồn."""
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise ValueError("Tọa độ không hợp lệ")
    start, end = pd.Timestamp(start_date), pd.Timestamp(end_date)
    if start > end or end >= pd.Timestamp.now(tz="UTC").normalize().tz_localize(None):
        raise ValueError("Chỉ tải khoảng lịch sử đã kết thúc")
    if model not in {"ecmwf_ifs", "era5"}:
        raise ValueError("Model nguồn chưa được hỗ trợ")
    destination = Path(destination)
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError("Thư mục download đã có dữ liệu; chọn thư mục mới")
    destination.mkdir(parents=True, exist_ok=True)
    variables = ["temperature_2m", "relative_humidity_2m", "surface_pressure", "cloud_cover", "wind_speed_10m"]
    frames, responses = [], []
    for year in range(start.year, end.year + 1):
        params = {"latitude": latitude, "longitude": longitude,
                  "start_date": str(max(start, pd.Timestamp(year, 1, 1)).date()),
                  "end_date": str(min(end, pd.Timestamp(year, 12, 31)).date()),
                  "hourly": ",".join(variables + ["precipitation"]), "models": model,
                  "timezone": "UTC", "wind_speed_unit": "ms", "temperature_unit": "celsius",
                  "precipitation_unit": "mm"}
        response = requests.get("https://archive-api.open-meteo.com/v1/archive", params=params, timeout=90)
        response.raise_for_status()
        payload = response.json()
        expected_units = dict(zip(variables + ["precipitation"], ["°C", "%", "hPa", "%", "m/s", "mm"]))
        if any(payload["hourly_units"].get(key) != value for key, value in expected_units.items()):
            raise ValueError("API trả đơn vị không đúng contract")
        raw_path = destination / f"{year}.json"
        write_json(raw_path, payload)
        responses.append({"file": raw_path.name, "sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
                          "request": params})
        frame = pd.DataFrame(payload["hourly"])
        frame.index = pd.to_datetime(frame.pop("time"), utc=True)
        frame = frame.rename(columns=dict(zip(variables + ["precipitation"], BASE_COLUMNS + ["precipitation_mm"])))
        frames.append(frame)
    frame, audit = validate_hourly(pd.concat(frames))
    metadata = {"provider": "open_meteo_archive", "source_kind": "historical_reconstruction",
                "latitude": latitude, "longitude": longitude, "model": model, "units": UNITS,
                "downloaded_at": datetime.now(timezone.utc).isoformat(), "responses": responses, "audit": audit}
    write_json(destination / "source.json", metadata)
    return frame, metadata
