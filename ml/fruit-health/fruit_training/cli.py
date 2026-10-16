"""CLI audit/smoke/train/test; chạy trong môi trường ML riêng."""

import argparse
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import torch

from .data import audit_dataset, prepare_dataset, smoke_subset
from .inference import FruitPredictor
from .train import TrainConfig, evaluate, train


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["audit", "smoke", "train", "test"])
    parser.add_argument("--data", default=os.getenv("FRUIT_DATA_ROOT", "data/raw/tomato-ripeness-v4"))
    parser.add_argument("--output", default=os.getenv("FRUIT_OUTPUT_ROOT", "outputs"))
    parser.add_argument("--manifest", help="Dùng manifest audit đã có; prepare vẫn kiểm tra hash nguồn")
    parser.add_argument("--data-yaml")
    parser.add_argument("--run-dir")
    parser.add_argument("--confirm-final-test", action="store_true")
    args = parser.parse_args()
    torch.set_num_threads(2)
    if args.command == "test":
        if not args.data_yaml or not args.run_dir:
            parser.error("test yêu cầu --data-yaml và --run-dir")
        print(evaluate(args.data_yaml, args.run_dir, split="test", allow_test=args.confirm_final_test))
        return
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    output = Path(args.output)
    if args.manifest:
        records = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    else:
        records, report = audit_dataset(args.data, output / f"audit-{stamp}")
        print(json.dumps({"raw_images": report["raw_images"], "clean_images": report["clean_images"],
                          "splits": report["clean_split_images"],
                          "excluded": dict(Counter(r["reason"] for r in report["excluded"]))}, indent=2))
    if args.command == "audit":
        return
    smoke = args.command == "smoke"
    if smoke:
        records = smoke_subset(records, per_split=16, seed=int(os.getenv("FRUIT_SEED", "42")))
    data_yaml = prepare_dataset(args.data, records, output / f"prepared-{stamp}")
    run = Path(args.run_dir) if args.run_dir else output / f"{'smoke' if smoke else 'yolo11n'}-{stamp}"
    config = TrainConfig(epochs=1 if smoke else 80, imgsz=320 if smoke else 640,
                         batch=4 if smoke else 16, workers=0 if smoke else 2, smoke=smoke,
                         seed=int(os.getenv("FRUIT_SEED", "42")))
    train(data_yaml, run, config=config)
    print(json.dumps(evaluate(data_yaml, run), indent=2))
    predictor = FruitPredictor(run)
    image = next((data_yaml.parent / "val" / "images").iterdir())
    prediction = predictor.predict(image, source="smoke" if smoke else "dataset", annotated_path=run / "example.jpg")
    print(json.dumps({"run_dir": str(run), "prediction": prediction}, indent=2))


if __name__ == "__main__":
    main()
