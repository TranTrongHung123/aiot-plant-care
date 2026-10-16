"""Bootstrap Colab và dữ liệu ZIP; không train hoặc mount Drive lúc import."""

import hashlib
import importlib.metadata as metadata
import json
import os
import platform
import subprocess
import sys
import zipfile
from pathlib import Path


def require_colab():
    if sys.platform != "linux" or "google.colab" not in sys.modules:
        raise RuntimeError("Chọn hosted runtime Google Colab; không dùng kernel Windows/local.")


def extract_zip_once(archive_path, destination):
    """Chỉ dùng lại bản giải nén hoàn tất từ cùng ZIP; không ghi đè dataset khác."""
    archive_path, destination = Path(archive_path), Path(destination).resolve()
    with archive_path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    marker = destination / ".source-sha256"
    if destination.exists() and any(destination.iterdir()):
        if marker.is_file() and marker.read_text(encoding="utf-8").strip() == digest:
            return destination
        raise FileExistsError("Dataset khác hoặc giải nén dở; chọn thư mục mới hoặc runtime mới.")
    with zipfile.ZipFile(archive_path) as archive:
        for item in archive.infolist():
            target = (destination / item.filename).resolve()
            if (not target.is_relative_to(destination) or "\\" in item.orig_filename
                    or (item.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError("ZIP chứa đường dẫn không hợp lệ hoặc symlink")
        destination.mkdir(parents=True, exist_ok=True)
        archive.extractall(destination)
    marker.write_text(digest, encoding="utf-8")
    return destination


def prepare_runtime(ml_root, *, require_gpu, install=True):
    require_colab()
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError("Lock bổ sung nhắm Python 3.12; chọn runtime Colab Python 3.12.")
    os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
    from packaging.requirements import Requirement
    from packaging.version import Version

    torch_before = {name: metadata.version(name) for name in ("torch", "torchvision")}
    if Version(torch_before["torch"].split("+")[0]) < Version("2.6"):
        raise RuntimeError("Cần runtime có torch >=2.6 và torchvision tương thích.")
    lock = Path(ml_root) / "requirements-colab.lock"
    requirements = [Requirement(line) for raw in lock.read_text(encoding="utf-8").splitlines()
                    if (line := raw.strip()) and not line.startswith("#")]
    wanted = [req for req in requirements if not req.marker or req.marker.evaluate()]
    changed = []
    for req in wanted:
        try:
            matches = metadata.version(req.name) in req.specifier
        except metadata.PackageNotFoundError:
            matches = False
        if not matches:
            changed.append(req.name)
    if changed and install:
        # Lock chứa dependency bắc cầu ngoài Torch; không cho pip resolve/thay CUDA.
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--no-deps", "-r", str(lock)])
    elif changed:
        raise RuntimeError(f"Cần INSTALL_DEPS=True để cài: {', '.join(changed)}")
    if torch_before != {name: metadata.version(name) for name in torch_before}:
        raise RuntimeError("Torch runtime bị thay đổi; hãy dùng runtime mới.")
    for module_name, distribution in (("numpy", "numpy"), ("pandas", "pandas"),
                                      ("PIL", "Pillow"), ("scipy", "scipy"),
                                      ("sklearn", "scikit-learn"), ("matplotlib", "matplotlib")):
        loaded = sys.modules.get(module_name)
        if loaded is not None and getattr(loaded, "__version__", None) != metadata.version(distribution):
            raise RuntimeError("Dependency trong bộ nhớ đã cũ: Runtime > Restart session, rồi chạy lại từ đầu.")
    for req in wanted:
        if metadata.version(req.name) not in req.specifier:
            raise RuntimeError(f"Dependency chưa đúng lock: {req.name}")
    # Chỉ kiểm tra dependency sử dụng, không kiểm tra toàn bộ hệ sinh thái Colab.
    for name in [req.name for req in wanted] + ["torch", "torchvision"]:
        for value in metadata.requires(name) or []:
            req = Requirement(value)
            if req.marker and not req.marker.evaluate({"extra": ""}):
                continue
            if metadata.version(req.name) not in req.specifier:
                raise RuntimeError(f"Runtime không tương thích: {name} yêu cầu {req}")
    import torch
    import torchvision
    if require_gpu and not torch.cuda.is_available():
        raise RuntimeError("Chọn Runtime > Change runtime type > GPU, rồi chạy lại từ đầu.")
    device = "cuda" if require_gpu else "cpu"
    boxes = torch.tensor([[0., 0., 10., 10.]], device=device)
    torchvision.ops.nms(boxes, torch.ones(1, device=device), 0.5)
    info = {"python": platform.python_version(), "torch": str(torch.__version__),
            "torchvision": str(torchvision.__version__), "cuda": torch.version.cuda,
            "device": torch.cuda.get_device_name(0) if require_gpu else "CPU Colab"}
    print(json.dumps(info, ensure_ascii=False, indent=2))
    return info
