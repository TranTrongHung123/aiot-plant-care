"""Audit YOLO box/polygon, gom ảnh trùng, loại rò rỉ và tạo bản detection riêng."""

import csv
import hashlib
import json
import random
import re
import shutil
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import yaml
from PIL import Image
from scipy.fft import dctn

NAMES = ["ripe", "unripe"]
SPLITS = {"train": "train", "val": "valid", "test": "test"}
EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def parse_labels(text):
    """Chuẩn hóa class xc yc w h; polygon chỉ lấy bbox bao ngoài, không sửa ảnh."""
    boxes, polygons = [], 0
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            values = np.array([float(value) for value in line.split()])
        except ValueError as error:
            raise ValueError(f"Dòng {number}: nhãn không phải số") from error
        if not np.isfinite(values).all() or len(values) < 5:
            raise ValueError(f"Dòng {number}: thiếu tọa độ hoặc số không hữu hạn")
        label = int(values[0])
        if values[0] != label or label not in range(len(NAMES)):
            raise ValueError(f"Dòng {number}: class ID không hợp lệ")
        coordinates = values[1:]
        if len(coordinates) == 4:
            x, y, width, height = coordinates
            bounds = np.array([x - width / 2, y - height / 2, x + width / 2, y + height / 2])
        elif len(coordinates) >= 6 and len(coordinates) % 2 == 0:
            if coordinates.min() < -1e-6 or coordinates.max() > 1 + 1e-6:
                raise ValueError(f"Dòng {number}: polygon vượt biên ảnh")
            points = coordinates.reshape(-1, 2)
            bounds = np.array([*points.min(axis=0), *points.max(axis=0)])
            polygons += 1
        else:
            raise ValueError(f"Dòng {number}: định dạng không phải box/polygon YOLO")
        if bounds.min() < -1e-6 or bounds.max() > 1 + 1e-6:
            raise ValueError(f"Dòng {number}: bbox vượt biên ảnh")
        x1, y1, x2, y2 = np.clip(bounds, 0, 1)
        if x2 <= x1 or y2 <= y1:
            raise ValueError(f"Dòng {number}: bbox suy biến")
        box = [label, float((x1 + x2) / 2), float((y1 + y2) / 2), float(x2 - x1), float(y2 - y1)]
        boxes.append(box)
    # Không cho annotation lặp tạo sai lệch số quả.
    keys = [tuple(round(v, 8) for v in box) for box in boxes]
    if len(keys) != len(set(keys)):
        raise ValueError("Annotation trùng trong cùng ảnh; cần rà soát")
    return boxes, polygons


def phash(image):
    gray = np.asarray(image.convert("L").resize((32, 32), Image.Resampling.LANCZOS), dtype=float)
    coefficients = dctn(gray, type=2, norm="ortho")[:8, :8].ravel()
    return sum(int(bit) << i for i, bit in enumerate(coefficients > np.median(coefficients[1:])))


def source_stem(path):
    # Roboflow .rf.<hash> là ID bản export, không phải một cảnh độc lập.
    return re.sub(r"\.rf\.[0-9a-f]+$", "", Path(path).stem, flags=re.IGNORECASE)


def audit_dataset(root, output, *, distance=4, groups_csv=None):
    root, output = Path(root).resolve(), Path(output)
    spec = yaml.safe_load((root / "data.yaml").read_text(encoding="utf-8"))
    names = spec.get("names")
    if isinstance(names, dict):
        names = [names[key] for key in sorted(names)]
    if names != NAMES or spec.get("nc", len(names)) != 2:
        raise ValueError("Dataset yêu cầu mapping 0=ripe, 1=unripe; không đoán lại nhãn")
    if not 0 <= distance <= 8:
        raise ValueError("Khoảng cách pHash phải thuộc [0,8]")
    manual_groups = {}
    if groups_csv:
        with Path(groups_csv).open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                if row["path"] in manual_groups or not row["group_id"].strip():
                    raise ValueError("CSV group_id có đường dẫn trùng hoặc ID rỗng")
                manual_groups[row["path"]] = row["group_id"]
    records, excluded, inventory, all_paths = [], [], {}, set()
    for split, directory in SPLITS.items():
        image_dir, label_dir = root / directory / "images", root / directory / "labels"
        if not image_dir.is_dir() or not label_dir.is_dir():
            raise FileNotFoundError(f"Thiếu images/labels: {directory}")
        images = sorted(p for p in image_dir.iterdir() if p.suffix.lower() in EXTENSIONS)
        inventory[split] = len(images)
        if len({p.stem for p in images}) != len(images):
            raise ValueError("Nhiều ảnh cùng stem; nhãn không xác định duy nhất")
        orphans = {p.stem for p in label_dir.glob("*.txt")} - {p.stem for p in images}
        if orphans:
            raise ValueError(f"Nhãn không có ảnh: {sorted(orphans)[:5]}")
        for path in images:
            relative = path.relative_to(root).as_posix()
            all_paths.add(relative)
            label_path = label_dir / f"{path.stem}.txt"
            try:
                if not label_path.is_file():
                    raise ValueError("Thiếu file nhãn; không tự coi là ảnh nền")
                boxes, polygons = parse_labels(label_path.read_text(encoding="utf-8-sig"))
                with Image.open(path) as original:
                    original.verify()
                with Image.open(path) as original:
                    if original.getexif().get(274, 1) != 1:
                        raise ValueError("EXIF orientation chưa chuẩn hóa; phải biến đổi nhãn cùng ảnh")
                    image = original.convert("RGB")
                    image.load()
                records.append({"path": relative, "label_path": label_path.relative_to(root).as_posix(),
                    "split": split, "boxes": boxes, "polygon_count": polygons,
                    "image_sha256": sha256(path), "label_sha256": sha256(label_path),
                    "pixel_sha256": hashlib.sha256(str(image.size).encode() + image.tobytes()).hexdigest(),
                    "phash": f"{phash(image):016x}", "source_stem": source_stem(path),
                    "manual_group": manual_groups.get(relative), "width": image.width, "height": image.height})
            except (ValueError, OSError, Image.DecompressionBombError) as error:
                excluded.append({"path": relative, "reason": "invalid_image_or_label", "detail": str(error)})
    if set(manual_groups) - all_paths:
        raise ValueError("CSV group_id có path không thuộc dataset")
    parent = list(range(len(records)))

    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(a, b):
        parent[find(b)] = find(a)

    seen, buckets, pairs = {}, defaultdict(list), []
    boundaries = np.linspace(0, 64, distance + 2, dtype=int).tolist()
    for i, record in enumerate(records):
        for field in ("image_sha256", "pixel_sha256", "source_stem", "manual_group"):
            if record[field] is not None:
                key = (field, record[field])
                if key in seen:
                    union(i, seen[key])
                seen[key] = i
        value, candidates, keys = int(record["phash"], 16), set(), []
        for band, (start, end) in enumerate(zip(boundaries, boundaries[1:])):
            key = (band, (value >> start) & ((1 << (end - start)) - 1))
            keys.append(key)
            candidates.update(buckets[key])
        for j in sorted(candidates):
            delta = (value ^ int(records[j]["phash"], 16)).bit_count()
            if delta <= distance:
                union(i, j)
                pairs.append({"a": records[j]["path"], "b": record["path"], "distance": delta})
        for key in keys:
            buckets[key].append(i)
    grouped = defaultdict(list)
    for i in range(len(records)):
        grouped[find(i)].append(records[i])
    clean, groups = [], []
    for members in grouped.values():
        group_id = hashlib.sha256("\n".join(sorted(r["path"] for r in members)).encode()).hexdigest()
        # Giữ split gốc; giữ holdout ưu tiên Test > Validation > Train, loại phía còn lại.
        owner = max((r["split"] for r in members), key={"train": 0, "val": 1, "test": 2}.get)
        annotation_sets = defaultdict(set)
        for row in members:
            canonical = sorted(tuple(round(float(v), 6) for v in box) for box in row["boxes"])
            annotation_sets[row["pixel_sha256"]].add(json.dumps(canonical))
        conflicting = {key for key, values in annotation_sets.items() if len(values) > 1}
        kept_pixels = set()
        for row in sorted(members, key=lambda r: r["path"]):
            reason = ("annotation_conflict" if row["pixel_sha256"] in conflicting else
                      "cross_split_group" if row["split"] != owner else
                      "exact_duplicate" if row["pixel_sha256"] in kept_pixels else None)
            if reason:
                excluded.append({"path": row["path"], "reason": reason, "group_id": group_id})
            else:
                clean.append({**row, "group_id": group_id})
                kept_pixels.add(row["pixel_sha256"])
        if len(members) > 1:
            groups.append({"group_id": group_id, "owner": owner, "paths": [r["path"] for r in members],
                           "original_splits": sorted({r["split"] for r in members})})
    clean.sort(key=lambda r: r["path"])
    assert_no_leakage(clean)
    report = {"inventory": inventory, "raw_images": sum(inventory.values()), "valid_images": len(records),
              "clean_images": len(clean), "raw_polygon_count": sum(r["polygon_count"] for r in records),
              "excluded": excluded, "groups": groups, "near_pairs": pairs, "phash_distance": distance,
              "names": NAMES, "source": spec.get("roboflow", {}),
              "class_box_counts": {}, "clean_split_images": {}, "negative_images": {},
              "limitations": "pHash/stem không chứng minh độc lập theo cảnh/video/cây; cần rà soát và CSV group_id."}
    for split in SPLITS:
        subset = [r for r in clean if r["split"] == split]
        counts = Counter(int(box[0]) for r in subset for box in r["boxes"])
        report["class_box_counts"][split] = {name: counts[i] for i, name in enumerate(NAMES)}
        report["clean_split_images"][split] = len(subset)
        report["negative_images"][split] = sum(not r["boxes"] for r in subset)
    write_json(output / "audit.json", report)
    write_json(output / "manifest.json", clean)
    for split in SPLITS:
        if not report["clean_split_images"][split] or min(report["class_box_counts"][split].values()) == 0:
            raise ValueError(f"Tập {split} không đủ hai lớp sau audit; xem audit.json")
    return clean, report


def assert_no_leakage(records):
    for field in ("group_id", "image_sha256", "pixel_sha256", "source_stem", "manual_group"):
        seen = {}
        for row in records:
            value = row.get(field)
            if value is not None:
                if value in seen and seen[value] != row["split"]:
                    raise ValueError(f"Rò rỉ {field}: {row['path']}")
                seen[value] = row["split"]


def smoke_subset(records, per_split=16, seed=42):
    if per_split < 2:
        raise ValueError("Smoke cần ít nhất 2 ảnh mỗi tập")
    rng, chosen = random.Random(seed), []
    for split in ("train", "val"):
        candidates = [r for r in records if r["split"] == split]
        rng.shuffle(candidates)
        selected = []
        for label in range(2):
            row = next(r for r in candidates if any(int(b[0]) == label for b in r["boxes"]))
            if row not in selected:
                selected.append(row)
        selected += [r for r in candidates if r not in selected][:max(0, per_split - len(selected))]
        chosen.extend(selected)
    return chosen


def manifest_fingerprint(records):
    return hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest()


def prepare_dataset(root, records, destination):
    """Copy ảnh/box chuẩn hóa vào nơi mới; không chạy trực tiếp YAML raw có đường dẫn ../."""
    root, destination = Path(root).resolve(), Path(destination).resolve()
    assert_no_leakage(records)
    fingerprint = manifest_fingerprint(records)
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError("Prepared dataset đã tồn tại; dùng lại qua verify_prepared hoặc chọn thư mục mới")
    destination.mkdir(parents=True, exist_ok=True)
    prepared = []
    for row in records:
        original = root / row["path"]
        if sha256(original) != row["image_sha256"] or sha256(root / row["label_path"]) != row["label_sha256"]:
            raise ValueError("Dataset thay đổi sau audit")
        identifier = hashlib.sha256(row["path"].encode()).hexdigest()[:24]
        image = destination / row["split"] / "images" / (identifier + original.suffix.lower())
        label = destination / row["split"] / "labels" / (identifier + ".txt")
        image.parent.mkdir(parents=True, exist_ok=True)
        label.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, image)
        label.write_text("".join(f"{int(b[0])} " + " ".join(f"{v:.8f}" for v in b[1:]) + "\n"
                                 for b in row["boxes"]), encoding="utf-8")
        prepared.append({**row, "prepared_image": image.relative_to(destination).as_posix(),
                         "prepared_label": label.relative_to(destination).as_posix(),
                         "prepared_label_sha256": sha256(label)})
    spec = {"path": str(destination), "train": "train/images", "val": "val/images", "nc": 2, "names": NAMES}
    if any(row["split"] == "test" for row in records):
        spec["test"] = "test/images"
    (destination / "data.yaml").write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
    write_json(destination / "manifest.json", {"fingerprint": fingerprint, "records": prepared})
    return destination / "data.yaml"


def verify_prepared(data_yaml):
    root = Path(data_yaml).resolve().parent
    metadata = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    spec = yaml.safe_load(Path(data_yaml).read_text(encoding="utf-8"))
    expected = {"path": str(root), "train": "train/images", "val": "val/images", "nc": 2, "names": NAMES}
    if any(row["split"] == "test" for row in metadata["records"]):
        expected["test"] = "test/images"
    if spec != expected:
        raise ValueError("Prepared YAML không khớp manifest/đường dẫn; không thực thi download từ YAML bên ngoài")
    assert_no_leakage(metadata["records"])
    original = [{k: v for k, v in row.items() if not k.startswith("prepared_")} for row in metadata["records"]]
    if manifest_fingerprint(original) != metadata["fingerprint"]:
        raise ValueError("Manifest fingerprint không khớp")
    for row in metadata["records"]:
        if sha256(root / row["prepared_image"]) != row["image_sha256"] or sha256(root / row["prepared_label"]) != row["prepared_label_sha256"]:
            raise ValueError("Prepared dataset đã bị sửa")
    for split in SPLITS:
        actual = {p.relative_to(root).as_posix() for folder in ("images", "labels")
                  for p in (root / split / folder).glob("*") if p.suffix.lower() in EXTENSIONS | {".txt"}}
        expected_files = {r[k] for r in metadata["records"] if r["split"] == split
                          for k in ("prepared_image", "prepared_label")}
        if actual != expected_files:
            raise ValueError("Prepared dataset có file thừa/thiếu so với manifest")
    return metadata
