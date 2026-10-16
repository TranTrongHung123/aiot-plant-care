"""Kiểm chứng rò rỉ, artifact và hai giai đoạn bằng ảnh tổng hợp trên CPU."""

import json
import shutil

import numpy as np
import pytest
import torch
from PIL import Image

from leaf_training.data import CLASSES, assert_no_leakage, audit_dataset, split_records
from leaf_training.data import subset_for_smoke
from leaf_training.inference import Predictor
from leaf_training.train import TrainConfig, evaluate, train


@pytest.fixture
def dataset(tmp_path):
    root = tmp_path / "data"
    rng = np.random.default_rng(123)
    for name in CLASSES:
        (root / name).mkdir(parents=True)
        for i in range(8):
            Image.fromarray(rng.integers(0, 256, (40, 40, 3), dtype=np.uint8)).save(root / name / f"{i}.png")
    return root


def test_audit_and_group_split(dataset, tmp_path):
    original = dataset / CLASSES[0] / "0.png"
    shutil.copyfile(original, dataset / CLASSES[0] / "duplicate.png")
    (dataset / CLASSES[1] / "corrupt.jpg").write_bytes(b"invalid")
    groups = tmp_path / "groups.csv"
    groups.write_text(f"path,leaf_id\n{CLASSES[2]}/0.png,leaf-1\n{CLASSES[2]}/1.png,leaf-1\n", encoding="utf-8")
    clean, report = audit_dataset(dataset, tmp_path / "audit", leaf_groups_csv=groups)
    assert len(report["corrupt"]) == 1
    assert len(report["excluded"]) == 1
    assert report["excluded"][0]["reason"] == "exact_duplicate"
    first, fingerprint = split_records(clean, tmp_path / "split")
    second, other = split_records(clean, tmp_path / "split2")
    assert first == second and fingerprint == other
    assert len({r["split"] for r in first if r["leaf_id"] == "leaf-1"}) == 1
    assert_no_leakage(first)
    assert {r["split"] for r in subset_for_smoke(first)} == {"train", "val"}
    with pytest.raises(ValueError, match="Rò rỉ"):
        assert_no_leakage([first[0], {**first[0], "split": "leaked"}])


def test_conflicting_labels_quarantined(dataset, tmp_path):
    shutil.copyfile(dataset / CLASSES[0] / "0.png", dataset / CLASSES[1] / "conflict.png")
    _, report = audit_dataset(dataset, tmp_path / "audit", distance=0)
    assert sum(r["reason"] == "label_conflict" for r in report["excluded"]) == 2


def test_near_duplicate_kept_in_same_group(dataset, tmp_path):
    path = dataset / CLASSES[0] / "0.png"
    with Image.open(path) as image:
        pixels = np.array(image)
    pixels[0, 0, 0] ^= 1
    Image.fromarray(pixels).save(path.with_name("near.png"))
    clean, report = audit_dataset(dataset, tmp_path / "audit")
    pair = [r for r in clean if r["class_name"] == CLASSES[0] and
            r["path"].split("/")[-1] in {"0.png", "near.png"}]
    assert len(pair) == 2 and pair[0]["pixel_sha256"] != pair[1]["pixel_sha256"]
    assert pair[0]["group_id"] == pair[1]["group_id"]
    assert report["near_pairs"]
    records, _ = split_records(clean, tmp_path / "audit")
    assert len({r["split"] for r in records if r["group_id"] == pair[0]["group_id"]}) == 1


def test_training_export_resume_and_inference(dataset, tmp_path, monkeypatch):
    torch.set_num_threads(2)
    clean, _ = audit_dataset(dataset, tmp_path / "audit", distance=0)
    records, _ = split_records(clean, tmp_path / "audit")
    records = subset_for_smoke(records, per_class=1)
    config = TrainConfig(epochs=2, warmup_epochs=1, batch_size=10, workers=0,
                         pretrained=False, smoke=True)
    artifact = train(dataset, records, tmp_path / "smoke", config)
    history = json.loads((artifact / "history.json").read_text())
    assert [r["stage"] for r in history] == ["head", "finetune"]
    # Mô phỏng ngắt đúng lúc chuyển từ frozen sang fine-tuning.
    import importlib

    training_module = importlib.import_module("leaf_training.train")
    real_run_epoch = training_module.run_epoch
    calls = 0

    def interrupt_epoch(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("simulated interruption")
        return real_run_epoch(*args, **kwargs)

    monkeypatch.setattr(training_module, "run_epoch", interrupt_epoch)
    with pytest.raises(RuntimeError, match="simulated interruption"):
        train(dataset, records, tmp_path / "interrupted", config)
    monkeypatch.setattr(training_module, "run_epoch", real_run_epoch)
    resumed = train(dataset, records, tmp_path / "interrupted", config, resume=True)
    baseline_state = torch.load(artifact / "last.pt", weights_only=True)["model"]
    resumed_state = torch.load(resumed / "last.pt", weights_only=True)["model"]
    assert all(torch.equal(value, resumed_state[key]) for key, value in baseline_state.items())
    predictor = Predictor(artifact, device="cpu")
    prediction = predictor.predict(dataset / records[0]["path"])
    assert prediction["smoke_only"] and 0 <= prediction["confidence"] <= 1
    assert prediction["label"] in CLASSES
    metrics, _ = evaluate(dataset, records, artifact)
    assert 0 <= metrics["macro_f1"] <= 1
    with pytest.raises(ValueError, match="Test"):
        evaluate(dataset, records, artifact, split="test", allow_test=True)
    train(dataset, records, artifact, config, resume=True)
    with pytest.raises(FileExistsError):
        train(dataset, records, artifact, config)
    (artifact / "best.pt").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="Checksum"):
        Predictor(artifact)
