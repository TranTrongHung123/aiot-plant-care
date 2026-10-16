"""Đặc trưng nhân quả: tại t chỉ dùng thông tin có timestamp <= t."""

import hashlib
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from .data import BASE_COLUMNS, validate_hourly


@dataclass(frozen=True)
class FeatureConfig:
    rain_threshold_mm: float = 0.1
    horizon_minutes: int = 60
    display_timezone: str = "Asia/Ho_Chi_Minh"
    schema_version: int = 1

    def __post_init__(self):
        if not np.isfinite(self.rain_threshold_mm) or self.rain_threshold_mm <= 0:
            raise ValueError("Ngưỡng giáng thủy phải dương")
        if self.horizon_minutes != 60:
            raise ValueError("Phiên bản này chỉ hỗ trợ 60 phút")


def build_features(hourly, config=None):
    config = config or FeatureConfig()
    hourly, _ = validate_hourly(hourly, require_target=False)
    features = hourly[BASE_COLUMNS].copy()
    for column in BASE_COLUMNS:
        for lag in (1, 3):
            features[f"{column}_lag{lag}h"] = hourly[column].shift(lag)
        features[f"{column}_mean6h"] = hourly[column].rolling(6, min_periods=6).mean()
        features[f"{column}_change3h"] = hourly[column] - hourly[column].shift(3)
    local = features.index.tz_convert(config.display_timezone)
    for name, values, period in (("hour", local.hour, 24), ("day_of_year", local.dayofyear, 365.25)):
        features[f"{name}_sin"] = np.sin(2 * np.pi * values / period)
        features[f"{name}_cos"] = np.cos(2 * np.pi * values / period)
    return features


def make_supervised(hourly, config=None):
    config = config or FeatureConfig()
    hourly, audit = validate_hourly(hourly)
    features = build_features(hourly, config)
    # precipitation[t] là tổng (t-1h,t]; mục tiêu tại t là precipitation[t+1h].
    future = hourly["precipitation_mm"].shift(-1)
    eligible = features.notna().all(axis=1) & future.notna()
    table = features.loc[eligible].copy()
    table["target"] = (future.loc[eligible] >= config.rain_threshold_mm).astype(int)
    table["target_end"] = table.index + pd.Timedelta(hours=1)
    table["previous_precipitation_mm"] = hourly.loc[eligible, "precipitation_mm"]
    if table.empty:
        raise ValueError("Không đủ lịch sử hợp lệ để tạo đặc trưng và nhãn")
    return table, {"input_audit": audit, "eligible_rows": len(table),
                   "excluded_rows": len(hourly) - len(table), "feature_names": list(features.columns),
                   "positive_rate": float(table.target.mean()), "config": asdict(config)}


PARTS = ("train", "early_stop", "calibration", "threshold", "test")


def chronological_split(table, *, embargo_hours=6):
    """60/10/10/10/10 theo thời gian; Validation gồm ba block độc lập."""
    if embargo_hours < 6:
        raise ValueError("Embargo tối thiểu 6 giờ theo lookback")
    table = table.sort_index()
    if table.index.has_duplicates or len(table) < 200:
        raise ValueError("Cần >=200 giờ hợp lệ, không trùng timestamp")
    total = len(table)
    boundaries = [table.index[int(total * fraction)] for fraction in (0.6, 0.7, 0.8, 0.9)]
    starts = [table.index[0], *boundaries]
    ends = [*boundaries, table.index[-1] + pd.Timedelta(hours=2)]
    splits = {}
    for i, name in enumerate(PARTS):
        start = starts[i] + (pd.Timedelta(hours=embargo_hours) if i else pd.Timedelta(0))
        mask = (table.index >= start) & (table.target_end < ends[i])
        splits[name] = table.loc[mask].copy()
        if splits[name].empty:
            raise ValueError(f"Tập {name} rỗng sau embargo")
        if i and splits[PARTS[i - 1]].target_end.max() >= splits[name].index.min():
            raise ValueError("Nhãn vượt ranh giới thời gian")
    return splits


def fingerprint(table):
    return hashlib.sha256(table.to_csv(float_format="%.12g").encode()).hexdigest()
