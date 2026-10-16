"""Kiểm tra polygon, annotation, split, manifest và ZIP trước khi train."""

import json
import shutil
import zipfile

import numpy as np
import pytest
from PIL import Image

from fruit_training.data import (assert_no_leakage, audit_dataset, parse_labels, prepare_dataset,
                                 smoke_subset, verify_prepared)
from fruit_training.download import extract_dataset


@pytest.fixture
def dataset(tmp_path):
    root = tmp_path / "raw"
    root.mkdir()
    (root / "data.yaml").write_text("nc: 2\nnames: [ripe, unripe]\n", encoding="utf-8")
    rng = np.random.default_rng(123)
    for split in ("train", "valid", "test"):
        for directory in ("images", "labels"):
            (root / split / directory).mkdir(parents=True)
        for index in range(5):
            stem = f"{split}_{index}"
            Image.fromarray(rng.integers(0, 256, (40, 40, 3), dtype=np.uint8)).save(root / split / "images" / f"{stem}.png")
            (root / split / "labels" / f"{stem}.txt").write_text(f"{index % 2} 0.5 0.5 0.5 0.5\n", encoding="utf-8")
    return root


def test_polygon_to_box_and_invalid_geometry():
    boxes, count = parse_labels("1 0.1 0.2 0.7 0.2 0.7 0.8 0.1 0.8\n")
    assert count == 1 and np.allclose(boxes[0], [1, 0.4, 0.5, 0.6, 0.6])
    assert parse_labels("") == ([], 0)
    for line in ("2 0.5 0.5 0.2 0.2", "0 nan 0.5 0.1 0.1", "0 0.1 0.1 0.8 0.8",
                 "0 0.5 0.5 0 0.1", "0 0.5 0.5 -0.1 0.1", "0 0 0 0.2 0.2 1.1 1"):
        with pytest.raises(ValueError):
            parse_labels(line)


def test_cross_split_duplicates_never_reach_train(dataset, tmp_path):
    original = dataset / "test/images/test_0.png"
    shutil.copyfile(original, dataset / "train/images/copy.png")
    shutil.copyfile(dataset / "test/labels/test_0.txt", dataset / "train/labels/copy.txt")
    clean, report = audit_dataset(dataset, tmp_path / "audit")
    assert any(row["path"] == "test/images/test_0.png" for row in clean)
    assert all(row["path"] != "train/images/copy.png" for row in clean)
    assert any(row["reason"] == "cross_split_group" for row in report["excluded"])
    assert_no_leakage(clean)
    with pytest.raises(ValueError, match="Rò rỉ"):
        assert_no_leakage([clean[0], {**clean[0], "split": "leaked"}])


def test_conflicts_missing_labels_and_smoke(dataset, tmp_path):
    shutil.copyfile(dataset / "test/images/test_0.png", dataset / "train/images/conflict.png")
    (dataset / "train/labels/conflict.txt").write_text("1 0.5 0.5 0.5 0.5\n")
    (dataset / "train/labels/train_4.txt").unlink()
    clean, report = audit_dataset(dataset, tmp_path / "audit", distance=0)
    reasons = [row["reason"] for row in report["excluded"]]
    assert reasons.count("annotation_conflict") == 2
    assert reasons.count("invalid_image_or_label") == 1
    selected = smoke_subset(clean, per_split=3)
    assert {r["split"] for r in selected} == {"train", "val"}
    for split in ("train", "val"):
        assert {int(b[0]) for r in selected if r["split"] == split for b in r["boxes"]} == {0, 1}


def test_prepare_hash_and_file_guard(dataset, tmp_path):
    clean, _ = audit_dataset(dataset, tmp_path / "audit", distance=0)
    path = prepare_dataset(dataset, smoke_subset(clean, per_split=2), tmp_path / "prepared")
    manifest = verify_prepared(path)
    assert len(manifest["records"]) == 4
    with pytest.raises(FileExistsError):
        prepare_dataset(dataset, clean, path.parent)
    label = path.parent / manifest["records"][0]["prepared_label"]
    label.write_text("")
    with pytest.raises(ValueError, match="bị sửa"):
        verify_prepared(path)


def test_zip_traversal_is_rejected(tmp_path):
    archive_path = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("../outside.txt", "bad")
    with pytest.raises(ValueError, match="đường dẫn"):
        extract_dataset(archive_path, tmp_path / "download")
    assert not (tmp_path / "outside.txt").exists()


def test_manifest_fingerprint_guard(dataset, tmp_path):
    clean, _ = audit_dataset(dataset, tmp_path / "audit", distance=0)
    path = prepare_dataset(dataset, clean, tmp_path / "prepared")
    metadata = json.loads((path.parent / "manifest.json").read_text(encoding="utf-8"))
    metadata["records"][0]["boxes"] = []
    (path.parent / "manifest.json").write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="fingerprint"):
        verify_prepared(path)
