from datetime import timedelta
from io import BytesIO

import pytest
from PIL import Image
from pydantic import ValidationError

from app.core.schemas import PumpRequest, fresh
from app.db.models import now
from app.integrations.storage import normalize_image


def test_stale_and_future_sensor_values_are_not_fresh():
    at = now()
    assert fresh(at, at)
    assert not fresh(None, at)
    assert not fresh(at - timedelta(seconds=21), at)
    assert not fresh(at + timedelta(seconds=6), at)


def test_pump_duration_is_bounded():
    from uuid import uuid4

    with pytest.raises(ValidationError):
        PumpRequest(command_id=uuid4(), action="pump_on", duration_seconds=16)


def test_fake_image_is_rejected_and_png_normalized():
    with pytest.raises(ValueError):
        normalize_image(b"not a jpeg")
    buf = BytesIO()
    Image.new("RGB", (40, 60), "green").save(buf, "PNG")
    data, width, height = normalize_image(buf.getvalue())
    assert data.startswith(b"\xff\xd8") and (width, height) == (40, 60)


def test_upload_applies_exif_before_stripping_metadata():
    image = Image.new("RGB", (40, 60), "green")
    exif = Image.Exif()
    exif[274] = 6
    buffer = BytesIO()
    image.save(buffer, "JPEG", exif=exif)
    data, width, height = normalize_image(buffer.getvalue())
    with Image.open(BytesIO(data)) as normalized:
        assert normalized.size == (60, 40) == (width, height)
        assert not normalized.getexif().get(274)
