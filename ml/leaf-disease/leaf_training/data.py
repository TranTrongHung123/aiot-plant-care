"""Kiểm kê, kiểm tra ảnh và chia tập theo nhóm trước augmentation."""

import csv
import hashlib
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps
from scipy.fft import dctn
from tqdm.auto import tqdm

CLASSES = sorted([
    "Tomato___Bacterial_spot", "Tomato___Early_blight", "Tomato___Late_blight",
    "Tomato___Leaf_Mold", "Tomato___Septoria_leaf_spot",
    "Tomato___Spider_mites Two-spotted_spider_mite", "Tomato___Target_Spot",
    "Tomato___Tomato_Yellow_Leaf_Curl_Virus", "Tomato___Tomato_mosaic_virus",
    "Tomato___healthy",
])
EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def discover_classes(root):
    """Không đoán một root duy nhất: hỗ trợ cả layout train/val/lớp."""
    root = Path(root).resolve()
    if not root.is_dir():
        raise FileNotFoundError(root)
    directories = [root, *sorted(p for p in root.rglob("*") if p.is_dir())]
    folders = [p for p in directories if p.name in CLASSES]
    found = {p.name for p in folders}
    if found != set(CLASSES):
        raise ValueError(f"Thiếu lớp: {sorted(set(CLASSES) - found)}; root={root}")
    inventory, files = [], []
    for folder in folders:
        images = sorted(p for p in folder.rglob("*") if p.suffix.lower() in EXTENSIONS)
        if not images:
            raise ValueError(f"Thư mục lớp rỗng: {folder}")
        inventory.append({"folder": folder.relative_to(root).as_posix(),
                          "class_name": folder.name, "images": len(images)})
        files.extend((p, folder.name) for p in images)
    # Không âm thầm bỏ ảnh trong một lớp lạ hoặc cấu trúc lồng lớp.
    all_images = {p for p in root.rglob("*") if p.suffix.lower() in EXTENSIONS}
    if len(files) != len({p for p, _ in files}) or all_images != {p for p, _ in files}:
        raise ValueError("Ảnh ngoài 10 lớp hoặc thư mục lớp lồng nhau; chọn root hẹp hơn.")
    return sorted(files), inventory


def phash(image):
    pixels = np.asarray(image.convert("L").resize((32, 32), Image.Resampling.LANCZOS))
    coefficients = dctn(pixels.astype(float), type=2, norm="ortho")[:8, :8].flatten()
    bits = coefficients > np.median(coefficients[1:])
    return sum(int(bit) << i for i, bit in enumerate(bits))


def audit_dataset(root, output, *, distance=4, leaf_groups_csv=None):
    """Gom SHA/pixel/pHash và leaf_id (nếu có); cách ly nhóm mâu thuẫn nhãn.

    pHash chỉ là heuristic, không chứng minh hai ảnh khác nhóm là khác lá.
    CSV tùy chọn có cột path (tương đối root), leaf_id; mọi ảnh cùng lá dùng cùng ID.
    """
    if not 0 <= distance <= 8:
        raise ValueError("distance phải thuộc [0, 8]")
    root, output = Path(root).resolve(), Path(output)
    files, inventory = discover_classes(root)
    leaf_ids = {}
    if leaf_groups_csv:
        with Path(leaf_groups_csv).open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                if row["path"] in leaf_ids or not row["leaf_id"].strip():
                    raise ValueError("CSV leaf_id có đường dẫn trùng hoặc ID rỗng")
                leaf_ids[row["path"]] = row["leaf_id"]
        unknown = set(leaf_ids) - {p.relative_to(root).as_posix() for p, _ in files}
        if unknown:
            raise ValueError(f"CSV có đường dẫn không tồn tại: {sorted(unknown)[:5]}")
    records, corrupt = [], []
    for path, label in tqdm(files, desc="Kiểm tra ảnh"):
        relative = path.relative_to(root).as_posix()
        try:
            with Image.open(path) as original:
                original.verify()
            with Image.open(path) as original:
                image = ImageOps.exif_transpose(original).convert("RGB")
                image.load()
            records.append({
                "path": relative, "class_name": label, "label": CLASSES.index(label),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "pixel_sha256": hashlib.sha256(
                    str(image.size).encode() + image.tobytes()).hexdigest(),
                "phash": f"{phash(image):016x}", "width": image.width, "height": image.height,
                "leaf_id": leaf_ids.get(relative),
            })
        except (OSError, ValueError, Image.DecompressionBombError) as error:
            corrupt.append({"path": relative, "error": str(error)})

    parent = list(range(len(records)))

    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(a, b):
        parent[find(b)] = find(a)

    seen, buckets, edges = {}, defaultdict(list), []
    # distance+1 đoạn: khoảng cách <= distance buộc có ít nhất một đoạn giống hệt.
    boundaries = np.linspace(0, 64, distance + 2, dtype=int).tolist()
    for i, record in enumerate(tqdm(records, desc="Gom ảnh trùng/gần trùng")):
        for field in ("sha256", "pixel_sha256", "leaf_id"):
            value = record[field]
            if value is not None:
                key = (field, value)
                if key in seen:
                    union(i, seen[key])
                seen[key] = i
        value = int(record["phash"], 16)
        candidates, keys = set(), []
        for band, (start, end) in enumerate(zip(boundaries, boundaries[1:])):
            key = (band, (value >> start) & ((1 << (end - start)) - 1))
            keys.append(key)
            candidates.update(buckets[key])
        for j in sorted(candidates):
            delta = (value ^ int(records[j]["phash"], 16)).bit_count()
            if delta <= distance:
                union(i, j)
                edges.append({"a": records[j]["path"], "b": record["path"], "distance": delta})
        for key in keys:
            buckets[key].append(i)
    groups = defaultdict(list)
    for i in range(len(records)):
        groups[find(i)].append(i)
    clean, excluded, group_report = [], [], []
    for indices in groups.values():
        group_id = hashlib.sha256("\n".join(sorted(records[i]["path"] for i in indices)).encode()).hexdigest()
        conflict = len({records[i]["label"] for i in indices}) > 1
        pixels = set()
        for i in indices:
            record = {**records[i], "group_id": group_id}
            if conflict or record["pixel_sha256"] in pixels:
                excluded.append({**record, "reason": "label_conflict" if conflict else "exact_duplicate"})
            else:
                clean.append(record)
            pixels.add(record["pixel_sha256"])
        if len(indices) > 1:
            group_report.append({"group_id": group_id, "size": len(indices), "label_conflict": conflict,
                                 "paths": [records[i]["path"] for i in indices]})
    clean.sort(key=lambda row: row["path"])
    report = {"folders": inventory, "raw_count": len(files), "valid_count": len(records),
              "clean_count": len(clean), "corrupt": corrupt, "excluded": excluded,
              "groups": group_report, "near_pairs": edges, "phash_distance": distance,
              "class_counts": dict(Counter(row["class_name"] for row in clean)),
              "leaf_metadata_count": len(leaf_ids),
              "limitation": "pHash không bảo đảm phát hiện cùng lá khác góc/ánh sáng; cần metadata và rà soát."}
    write_json(output / "audit.json", report)
    write_json(output / "inventory.json", records)
    write_json(output / "clean.json", clean)
    if {row["class_name"] for row in clean} != set(CLASSES):
        raise ValueError("Có lớp không còn ảnh sau kiểm tra; xem audit.json")
    return clean, report


def split_records(records, output, seed=42):
    rng = random.Random(seed)
    assignments = {}
    for label in range(len(CLASSES)):
        groups = sorted({r["group_id"] for r in records if r["label"] == label})
        if len(groups) < 3:
            raise ValueError(f"Lớp {CLASSES[label]} cần ít nhất 3 nhóm độc lập")
        rng.shuffle(groups)
        count = max(1, round(len(groups) * 0.15))
        for i, group in enumerate(groups):
            assignments[group] = "test" if i < count else "val" if i < 2 * count else "train"
    result = [{**r, "split": assignments[r["group_id"]]} for r in records]
    assert_no_leakage(result)
    fingerprint = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
    write_json(Path(output) / "split.json", {"seed": seed, "fingerprint": fingerprint,
               "target_group_ratios": [0.7, 0.15, 0.15], "records": result})
    return result, fingerprint


def assert_no_leakage(records):
    for field in ("group_id", "sha256", "pixel_sha256", "leaf_id"):
        owners = {}
        for row in records:
            value = row.get(field)
            if value is not None:
                if value in owners and owners[value] != row["split"]:
                    raise ValueError(f"Rò rỉ qua {field}: {row['path']}")
                owners[value] = row["split"]


def subset_for_smoke(records, per_class=8, seed=42):
    rng = random.Random(seed)
    result = []
    for split in ("train", "val"):
        for label in range(len(CLASSES)):
            candidates = [r for r in records if r["split"] == split and r["label"] == label]
            result.extend(rng.sample(candidates, min(per_class, len(candidates))))
    return result
