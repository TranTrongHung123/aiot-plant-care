"""Kiểm tra hợp đồng inference và khóa Test, không cần tải weights trong unit test."""

from types import SimpleNamespace

import pytest
import torch
from PIL import Image

from fruit_training import inference
from fruit_training.data import sha256, write_json
from fruit_training.train import TrainConfig, evaluate, train


@pytest.fixture
def artifact(tmp_path):
    root = tmp_path / "artifact"
    root.mkdir()
    (root / "best.pt").write_bytes(b"unit-test-placeholder-not-a-model")
    write_json(root / "class_mapping.json", {"0": "ripe", "1": "unripe"})
    write_json(root / "preprocessing.json", {"imgsz": 320, "confidence": 0.25, "nms_iou": 0.7, "max_det": 3})
    write_json(root / "metadata.json", {
        "schema_version": 1, "task": "fruit_ripeness_detection", "model_version": "unit-test",
        "smoke_only": True, "dataset_fingerprint": "unit-test-fingerprint",
        "artifact_sha256": {name: sha256(root / name)
                            for name in ("best.pt", "class_mapping.json", "preprocessing.json")},
    })
    return root


def test_checksum_rejected_before_loading_model(artifact, monkeypatch):
    def must_not_load():
        pytest.fail("Weights hỏng không được đưa vào loader")
    monkeypatch.setattr(inference, "load_yolo", must_not_load)
    (artifact / "best.pt").write_bytes(b"modified")
    with pytest.raises(ValueError, match="Checksum"):
        inference.FruitPredictor(artifact)


def test_inference_box_count_and_source_contract(artifact, tmp_path, monkeypatch):
    class FakeModel:
        names = {0: "ripe", 1: "unripe"}

        def predict(self, image, **kwargs):
            assert image.mode == "RGB" and kwargs["conf"] == 0.25
            assert kwargs["augment"] is False and kwargs["rect"] is False
            return [SimpleNamespace(boxes=SimpleNamespace(
                xyxy=torch.tensor([[10., 10., 30., 30.], [40., 40., 60., 60.], [70., 70., 90., 90.]]),
                xyxyn=torch.tensor([[.1, .1, .3, .3], [.4, .4, .6, .6], [.7, .7, .9, .9]]),
                conf=torch.tensor([.8, .9, .7]), cls=torch.tensor([0., 0., 1.]),
            ))]

    monkeypatch.setattr(inference, "load_yolo", lambda: lambda *args, **kwargs: FakeModel())
    image = tmp_path / "input.png"
    Image.new("RGBA", (100, 100)).save(image)
    result = inference.FruitPredictor(artifact, device="cpu").predict(image, source="simulation")
    assert result["count"] == 3
    assert result["counts_by_class"] == {"ripe": 2, "unripe": 1}
    assert result["count_may_be_truncated"] is True
    assert result["source"] == "simulation" and result["smoke_only"] is True
    assert result["production_ready"] is False
    assert result["predicted_at"].endswith("+00:00")
    assert result["detections"][0]["bbox_xyxy"] == [10., 10., 30., 30.]


def test_smoke_cannot_train_with_test_records(tmp_path, monkeypatch):
    monkeypatch.setattr("fruit_training.train.verify_prepared", lambda path: {"records": [{"split": "test"}]})
    with pytest.raises(ValueError, match="không được chứa Test"):
        train(tmp_path / "data.yaml", tmp_path / "run", config=TrainConfig(smoke=True))
    assert not (tmp_path / "run").exists()


@pytest.mark.parametrize("smoke,allow_test", [(True, True), (True, False), (False, False)])
def test_final_test_requires_full_model_and_explicit_flag(tmp_path, monkeypatch, smoke, allow_test):
    monkeypatch.setattr("fruit_training.train.verify_prepared", lambda path: {"fingerprint": "abc"})
    monkeypatch.setattr(inference, "FruitPredictor", lambda path: SimpleNamespace(
        metadata={"dataset_fingerprint": "abc", "smoke_only": smoke}))
    with pytest.raises(ValueError, match="Test chỉ mở"):
        evaluate(tmp_path / "data.yaml", tmp_path, split="test", allow_test=allow_test)
