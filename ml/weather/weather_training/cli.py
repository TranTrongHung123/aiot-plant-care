"""Chạy audit/smoke/train/evaluate từ môi trường ML riêng."""

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .data import load_open_meteo_csv, write_json
from .features import FeatureConfig, make_supervised
from .inference import RainPredictor
from .train import TrainConfig, evaluate_test, train


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["audit", "smoke", "train", "test"])
    parser.add_argument("--data", default=os.getenv("WEATHER_DATA_PATH", "data/raw/open-meteo.csv"))
    parser.add_argument("--output", default=os.getenv("WEATHER_OUTPUT_ROOT", "outputs"))
    parser.add_argument("--run-dir")
    parser.add_argument("--confirm-final-test", action="store_true")
    args = parser.parse_args()
    config = FeatureConfig(rain_threshold_mm=float(os.getenv("WEATHER_RAIN_THRESHOLD_MM", "0.1")))
    frame, source = load_open_meteo_csv(args.data)
    table, report = make_supervised(frame, config)
    root = Path(args.output)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    audit_dir = root / f"audit-{stamp}"
    write_json(audit_dir / "source.json", source)
    write_json(audit_dir / "features.json", report)
    print(json.dumps({"audit": source["audit"], "eligible_rows": len(table)}, indent=2))
    if args.command == "audit":
        return
    if args.command == "test":
        if not args.run_dir:
            parser.error("test yêu cầu --run-dir")
        print(json.dumps(evaluate_test(table, args.run_dir, allow_test=args.confirm_final_test), indent=2))
        return
    smoke = args.command == "smoke"
    # Smoke dùng 365 ngày đầu; không lấy mẫu ngẫu nhiên xuyên thời gian.
    chosen = table.loc[table.index < table.index.min() + pd.Timedelta(days=365)] if smoke else table
    run = Path(args.run_dir) if args.run_dir else root / f"{'smoke' if smoke else 'xgboost'}-{stamp}"
    train(chosen, report["feature_names"], run, feature_config=config,
          config=TrainConfig(seed=int(os.getenv("WEATHER_SEED", "42")),
                             num_boost_round=20 if smoke else 500,
                             early_stopping_rounds=5 if smoke else 30, smoke=smoke), provenance=source)
    predictor = RainPredictor(run)
    reference = chosen.index[-1]
    prediction = predictor.predict(frame.loc[:reference], as_of=reference, source="open_meteo_archive")
    write_json(run / "smoke_prediction.json" if smoke else run / "example_prediction.json", prediction)
    print(json.dumps({"run_dir": str(run), "prediction": prediction}, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
