import io

import urllib3
from minio import Minio
from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.settings import settings

storage = Minio(
    settings.minio_endpoint,
    access_key=settings.minio_app_user,
    secret_key=settings.minio_app_password,
    secure=settings.minio_secure,
    http_client=urllib3.PoolManager(timeout=urllib3.Timeout(connect=3, read=10), retries=1),
)
Image.MAX_IMAGE_PIXELS = 16_000_000


def normalize_image(data: bytes):
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in {"JPEG", "PNG"}:
                raise ValueError("Chỉ hỗ trợ JPEG hoặc PNG")
            if image.width * image.height > 16_000_000:
                raise ValueError("Ảnh tối đa 16 megapixel")
            image.load()
            output = io.BytesIO()
            normalized = ImageOps.exif_transpose(image).convert("RGB")
            normalized.save(output, format="JPEG", quality=90)
            return output.getvalue(), normalized.width, normalized.height
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("Tệp không phải ảnh JPEG/PNG hợp lệ") from exc
