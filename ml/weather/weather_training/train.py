"""XGBoost, early stopping, calibration và chọn threshold trên các block riêng."""

import hashlib
import importlib.metadata
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.calibration import calibration_curve
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (accuracy_score, auc, average_precision_score, brier_score_loss,
                             classification_report, confusion_matrix, f1_score, log_loss,
                             precision_recall_curve, precision_score, recall_score, roc_auc_score)

from .calibration import calibrate, logit
from .data import write_json
from .features import FeatureConfig, chronological_split, fingerprint


@dataclass(frozen=True)
class TrainConfig:
    seed: int = 42
    num_boost_round: int = 500
    early_stopping_rounds: int = 30
    max_depth: int = 4
    learning_rate: float = 0.05
    nthread: int = 2
    device: str = "cpu"
    smoke: bool = False


def metrics(labels, probabilities, threshold):
    labels, probabilities = np.asarray(labels), np.asarray(probabilities)
    predictions = probabilities >= threshold
    two_classes = len(np.unique(labels)) == 2
    precision, recall, _ = precision_recall_curve(labels, probabilities)
    return {
        "rows": len(labels), "positive_rate": float(labels.mean()), "threshold": float(threshold),
        "accuracy": float(accuracy_score(labels, predictions)),
        "precision": float(precision_score(labels, predictions, zero_division=0)),
        "recall": float(recall_score(labels, predictions, zero_division=0)),
        "f1": float(f1_score(labels, predictions, zero_division=0)),
        "macro_f1": float(f1_score(labels, predictions, average="macro", labels=[0, 1], zero_division=0)),
        "average_precision": float(average_precision_score(labels, probabilities)) if two_classes else None,
        "pr_auc_trapezoid": float(auc(recall, precision)) if two_classes else None,
        "roc_auc": float(roc_auc_score(labels, probabilities)) if two_classes else None,
        "brier_score": float(brier_score_loss(labels, probabilities)),
        "log_loss": float(log_loss(labels, probabilities, labels=[0, 1])),
        "confusion_matrix": confusion_matrix(labels, predictions, labels=[0, 1]).tolist(),
        "classification_report": classification_report(labels, predictions, labels=[0, 1],
            target_names=["dry", "precipitation"], output_dict=True, zero_division=0),
    }


def evaluate_block(block, raw, calibrated, threshold, train_prior, rain_threshold):
    labels = block.target.to_numpy()
    reports = {"xgboost_calibrated": metrics(labels, calibrated, threshold),
               "xgboost_raw": metrics(labels, raw, 0.5),
               "climatology": metrics(labels, np.full(len(labels), train_prior), 0.5),
               "always_dry": metrics(labels, np.zeros(len(labels)), 0.5)}
    valid = block.previous_precipitation_mm.notna()
    if valid.any():
        reports["persistence"] = metrics(labels[valid],
            (block.loc[valid, "previous_precipitation_mm"] >= rain_threshold).astype(float), 0.5)
        reports["persistence"]["coverage"] = float(valid.mean())
    return reports


def plot_evaluation(labels, probabilities, threshold, destination, title):
    figure, axes = plt.subplots(1, 3, figsize=(14, 4))
    precision, recall, _ = precision_recall_curve(labels, probabilities)
    axes[0].plot(recall, precision)
    axes[0].axhline(np.mean(labels), color="gray", linestyle="--", label="Tỷ lệ có giáng thủy")
    axes[0].set(xlabel="Recall", ylabel="Precision", title="Precision–Recall")
    axes[0].legend()
    observed, predicted = calibration_curve(labels, probabilities, n_bins=10, strategy="quantile")
    axes[1].plot(predicted, observed, "o-")
    axes[1].plot([0, 1], [0, 1], "--", color="gray")
    axes[1].set(xlabel="Xác suất dự đoán", ylabel="Tỷ lệ thực tế", title="Calibration")
    matrix = confusion_matrix(labels, probabilities >= threshold, labels=[0, 1])
    axes[2].imshow(matrix, cmap="Blues")
    axes[2].set(xticks=[0, 1], yticks=[0, 1], xlabel="Dự đoán", ylabel="Nhãn thật", title="Confusion matrix")
    for i in range(2):
        for j in range(2):
            axes[2].text(j, i, str(matrix[i, j]), ha="center", va="center")
    figure.suptitle(title)
    figure.tight_layout()
    figure.savefig(destination, dpi=150)
    plt.close(figure)


def train(table, feature_names, run_dir, *, feature_config=None, config=None, provenance=None):
    config, feature_config = config or TrainConfig(), feature_config or FeatureConfig()
    if config.num_boost_round < 1 or config.early_stopping_rounds < 1:
        raise ValueError("Số vòng boosting/early stopping phải dương")
    if config.device not in {"cpu", "cuda"}:
        raise ValueError("device phải là cpu hoặc cuda")
    forbidden = {"target", "target_end", "previous_precipitation_mm"}
    if forbidden.intersection(feature_names) or not feature_names:
        raise ValueError("Không được đưa nhãn hoặc cột baseline vào đặc trưng")
    splits = chronological_split(table)
    for name in ("train", "early_stop", "calibration", "threshold"):
        if set(splits[name].target.unique()) != {0, 1}:
            raise ValueError(f"Tập {name} cần có cả hai lớp; dùng khoảng thời gian dài hơn")
    run_dir = Path(run_dir)
    if run_dir.exists() and any(run_dir.iterdir()):
        raise FileExistsError("Không ghi đè run cũ; chọn tên mới")
    run_dir.mkdir(parents=True, exist_ok=True)
    training, early = splits["train"], splits["early_stop"]
    train_prior = float(training.target.mean())
    params = {"objective": "binary:logistic", "eval_metric": ["logloss", "aucpr"],
              "tree_method": "hist", "device": config.device, "max_depth": config.max_depth,
              "eta": config.learning_rate, "subsample": 0.9, "colsample_bytree": 0.9,
              "seed": config.seed, "nthread": config.nthread,
              "scale_pos_weight": float((training.target == 0).sum() / (training.target == 1).sum())}
    history = {}
    matrices = [xgb.DMatrix(part[feature_names], label=part.target) for part in (training, early)]
    booster = xgb.train(params, matrices[0], num_boost_round=config.num_boost_round,
        evals=[(matrices[0], "train"), (matrices[1], "early_stop")], evals_result=history,
        callbacks=[xgb.callback.EarlyStopping(rounds=config.early_stopping_rounds,
                    metric_name="aucpr", data_name="early_stop", maximize=True, save_best=True)],
        verbose_eval=False)
    calibration_block, threshold_block = splits["calibration"], splits["threshold"]
    calibration_raw = booster.predict(xgb.DMatrix(calibration_block[feature_names]))
    sigmoid = LogisticRegression(random_state=config.seed)
    sigmoid.fit(logit(calibration_raw).reshape(-1, 1), calibration_block.target)
    calibration = {"method": "sigmoid_logit", "coefficient": float(sigmoid.coef_[0, 0]),
                   "intercept": float(sigmoid.intercept_[0]), "clip_epsilon": 1e-6}
    raw = booster.predict(xgb.DMatrix(threshold_block[feature_names]))
    probabilities = calibrate(raw, calibration)
    # Lưới cố định, chỉ chọn trên block threshold, không đọc Test.
    thresholds = np.linspace(0.05, 0.95, 91)
    scores = [f1_score(threshold_block.target, probabilities >= value, zero_division=0) for value in thresholds]
    best = max(range(len(scores)), key=lambda i: (scores[i], -abs(thresholds[i] - 0.5)))
    threshold = float(thresholds[best])
    reports = evaluate_block(threshold_block, raw, probabilities, threshold, train_prior,
                             feature_config.rain_threshold_mm)
    booster.save_model(run_dir / "model.ubj")
    write_json(run_dir / "calibration.json", calibration)
    write_json(run_dir / "feature_config.json", asdict(feature_config))
    write_json(run_dir / "history.json", history)
    write_json(run_dir / "validation_metrics.json", reports)
    write_json(run_dir / "threshold_search.json", [{"threshold": float(t), "f1": float(s)}
                                                    for t, s in zip(thresholds, scores)])
    manifest = {}
    for name, block in splits.items():
        manifest[name] = {"rows": len(block), "start": block.index.min().isoformat(),
                          "end": block.index.max().isoformat(), "fingerprint": fingerprint(block)}
        block.index.to_series().to_csv(run_dir / f"{name}_timestamps.csv", index=False)
    metadata = {
        "schema_version": 1, "model_version": run_dir.name, "model_source": "trained",
        "is_mock": False, "smoke_only": config.smoke, "production_ready": False,
        "created_at": datetime.now(timezone.utc).isoformat(), "feature_names": feature_names,
        "feature_config": asdict(feature_config), "training_config": asdict(config),
        "threshold": threshold, "train_prior": train_prior, "best_iteration": int(booster.best_iteration),
        "target": "precipitation_next_60m", "target_interval": "(t, t+60m]",
        "rain_proxy": True, "label_definition": f"precipitation[t+1h] >= {feature_config.rain_threshold_mm} mm",
        "split_manifest": manifest, "data_fingerprint": fingerprint(table), "embargo_hours": 6,
        "provenance": provenance or {},
        "environment": {name: importlib.metadata.version(name) for name in
                        ("xgboost", "numpy", "pandas", "scikit-learn")},
        "artifact_sha256": {name: hashlib.sha256((run_dir / name).read_bytes()).hexdigest()
                            for name in ("model.ubj", "calibration.json", "feature_config.json")},
        "limitations": ["precipitation gồm cả tuyết; đây là nhãn đại diện cho mưa",
                        "Đánh giá hồi cứu trên dữ liệu tái dựng, chưa chứng minh thời điểm sẵn có",
                        "Chưa đánh giá sai lệch nguồn OpenWeather/cảm biến tại cây"],
    }
    write_json(run_dir / "metadata.json", metadata)
    plot_evaluation(threshold_block.target, probabilities, threshold,
                    run_dir / "validation.png", "Validation — block chọn threshold")
    figure, axes = plt.subplots(1, 2, figsize=(10, 4))
    for axis, metric in zip(axes, ("logloss", "aucpr")):
        for part in ("train", "early_stop"):
            axis.plot(history[part][metric], label=part)
        axis.axvline(booster.best_iteration, color="gray", linestyle="--")
        axis.set(xlabel="Boosting round", title=metric)
        axis.legend()
    figure.tight_layout()
    figure.savefig(run_dir / "history.png", dpi=150)
    plt.close(figure)
    gains = booster.get_score(importance_type="gain")
    write_json(run_dir / "feature_importance.json", {name: float(gains.get(name, 0)) for name in feature_names})
    return run_dir


def evaluate_test(table, run_dir, *, allow_test=False):
    from .inference import RainPredictor

    run_dir = Path(run_dir)
    predictor = RainPredictor(run_dir)
    metadata = predictor.metadata
    if not allow_test or metadata["smoke_only"]:
        raise ValueError("Test chỉ mở sau khi chốt thí nghiệm đầy đủ; không mở cho smoke")
    if (run_dir / "test_metrics.json").exists():
        raise FileExistsError("Test đã đánh giá; đọc báo cáo cũ")
    if fingerprint(table) != metadata["data_fingerprint"]:
        raise ValueError("Dữ liệu/manifest thay đổi so với run")
    block = chronological_split(table)["test"]
    raw = predictor.model.predict(xgb.DMatrix(block[metadata["feature_names"]]))
    probabilities = calibrate(raw, predictor.calibration)
    result = evaluate_block(block, raw, probabilities, metadata["threshold"], metadata["train_prior"],
                            metadata["feature_config"]["rain_threshold_mm"])
    write_json(run_dir / "test_metrics.json", result)
    pd.DataFrame({"target": block.target, "probability": probabilities,
                  "prediction": probabilities >= metadata["threshold"]}).to_csv(run_dir / "test_predictions.csv")
    plot_evaluation(block.target, probabilities, metadata["threshold"], run_dir / "test.png", "Test giữ riêng")
    return result
