"""Tải export Roboflow v4 hoặc giải nén ZIP local, không in credential."""

import os
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import requests


def extract_dataset(zip_path, destination):
    destination = Path(destination).resolve()
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError("Thư mục dataset đã có dữ liệu; không ghi đè")
    with zipfile.ZipFile(zip_path) as archive:
        if sum(item.file_size for item in archive.infolist()) > 5 * 1024**3:
            raise ValueError("ZIP quá lớn cho dataset v4 dự kiến")
        for item in archive.infolist():
            target = (destination / item.filename).resolve()
            if not target.is_relative_to(destination) or (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("ZIP chứa đường dẫn không hợp lệ hoặc symlink")
        destination.mkdir(parents=True, exist_ok=True)
        archive.extractall(destination)
    roots = sorted(p.parent for p in destination.rglob("data.yaml"))
    if len(roots) != 1:
        raise ValueError("Cần đúng một data.yaml trong export")
    return roots[0]


def download_roboflow(destination):
    key = os.getenv("ROBOFLOW_API_KEY")
    if not key:
        raise ValueError("Thiếu ROBOFLOW_API_KEY; đặt biến môi trường hoặc dùng dataset local")
    destination = Path(destination)
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError("Dataset đã tồn tại; không ghi đè")
    archive_path = destination.with_suffix(".zip")
    if archive_path.exists():
        raise FileExistsError("ZIP download đã tồn tại; chọn đường dẫn mới hoặc dùng extract_dataset")
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        response = requests.get("https://api.roboflow.com/uni-zkvyc/tomato-ripeness-um0yl/4/yolov8",
                                params={"api_key": key}, timeout=60)
        response.raise_for_status()
        link = response.json()["export"]["link"]
        if urlparse(link).scheme != "https":
            raise ValueError("Export không phải HTTPS")
        with requests.get(link, stream=True, timeout=120) as stream:
            stream.raise_for_status()
            with archive_path.open("xb") as output:
                for chunk in stream.iter_content(1024 * 1024):
                    output.write(chunk)
    except (requests.RequestException, KeyError, ValueError):
        raise RuntimeError("Tải Roboflow chưa thành công/format chưa sẵn sàng. Kiểm tra key/quyền hoặc dùng ZIP local.") from None
    return extract_dataset(archive_path, destination)
