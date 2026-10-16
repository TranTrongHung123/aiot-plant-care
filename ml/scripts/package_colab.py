"""Đóng gói mã nguồn Colab bằng stdlib; dataset chỉ thêm khi yêu cầu rõ ràng."""

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

TASKS = {"leaf-disease": "leaf_training", "fruit-health": "fruit_training", "weather": "weather_training"}


def source_files(ml_root):
    ml = Path(ml_root)
    files = [ml / name for name in ("README.md", "colab_runtime.py", "pyproject.toml",
             "requirements-colab.in", "requirements-colab.lock", "requirements-runtime.in",
             "requirements-dev.in", "requirements-dev.lock", ".env.example")]
    files += list((ml / "tests").glob("*.py"))
    files += list((ml / "scripts").glob("*.py"))
    for task, module in TASKS.items():
        base = ml / task
        files.append(base / "README.md")
        if (base / ".env.example").exists():
            files.append(base / ".env.example")
        for folder, pattern in ((module, "*.py"), ("tests", "*.py"),
                                ("scripts", "*.py"), ("notebooks", "*.ipynb")):
            files += list((base / folder).glob(pattern))
    for path in files:
        if path.is_symlink() or not path.resolve().is_relative_to(ml.resolve()):
            raise ValueError(f"Nguồn ngoài ml/: {path}")
    return sorted(files)


def write_archive(path, files, base):
    path.parent.mkdir(parents=True, exist_ok=True)
    expected = {file.relative_to(base).as_posix(): file for file in files}
    if path.exists():
        with zipfile.ZipFile(path) as archive:
            if (set(archive.namelist()) != set(expected)
                    or any(archive.read(name) != file.read_bytes() for name, file in expected.items())):
                raise FileExistsError(f"Archive khác nội dung; dùng --output với tên mới: {path}")
    else:
        with zipfile.ZipFile(path, "x", zipfile.ZIP_DEFLATED) as archive:
            for name, file in expected.items():
                archive.write(file, name)
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"file": str(path), "sha256": digest, "bytes": path.stat().st_size}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--include-datasets", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    output = args.output or root / "ml/dist/aiot-ml-colab.zip"
    reports = [write_archive(output, source_files(root / "ml"), root / "ml")]
    if args.include_datasets:
        for name, relative in (("plantvillage.zip", "leaf-disease/data/raw/plantvillage"),
                               ("tomato-ripeness-v4.zip", "fruit-health/data/raw/tomato-ripeness-v4")):
            base = root / "ml" / relative
            if not base.is_dir():
                raise FileNotFoundError(base)
            files = sorted(p for p in base.rglob("*") if p.is_file())
            if any(p.is_symlink() or not p.resolve().is_relative_to(base.resolve()) for p in files):
                raise ValueError("Dataset chứa symlink hoặc đường dẫn ngoài thư mục nguồn")
            reports.append(write_archive(output.parent / name, files, base))
        print("Upload CSV riêng: ml/weather/data/raw/open-meteo.csv")
    print(json.dumps(reports, indent=2))


if __name__ == "__main__":
    main()
