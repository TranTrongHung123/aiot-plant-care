import logging
import time
from datetime import timedelta

from sqlalchemy import and_, or_, select

from app.core.settings import settings
from app.db.models import Job, PlantImage, now
from app.db.session import Session
from app.integrations.storage import storage
from app.integrations.weather import collect_weather
from app.modules.trained_ai import InferenceUnavailable
from app.modules.weather_control import enqueue_live_forecast
from app.workers.runtime import runtime

log = logging.getLogger(__name__)


def run_once():
    with Session.begin() as db:
        job = db.scalar(
            select(Job)
            .where(
                or_(
                    Job.status == "queued",
                    and_(Job.status == "running", Job.lease_until < now()),
                )
            )
            .order_by(Job.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if not job:
            return False
        if job.provider != "trained":
            job.status, job.error = "failed", "Mock AI đã gỡ bỏ; hãy tạo yêu cầu mới"
            job.finished_at, job.lease_until = now(), None
            return True
        if job.attempts >= 3:
            job.status, job.error = "failed", "Đã hết số lần thử sau lỗi/timeout"
            job.finished_at = now()
            return True
        job.status, job.attempts = "running", job.attempts + 1
        job.lease_until = now() + timedelta(seconds=180)
        job_id, attempt, task, inputs = job.id, job.attempts, job.task, job.input
        version, source = job.model_version, job.source
        image = db.get(PlantImage, job.image_id) if job.image_id else None
        object_key = image.object_key if image else None
    try:
        data = b""
        if object_key:
            response = storage.get_object(settings.minio_images_bucket, object_key)
            try:
                data = response.read()
            finally:
                response.close()
                response.release_conn()
        result = runtime.predict(task, inputs, data, source=source, model_version=version)
        with Session.begin() as db:
            current = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if current.attempts == attempt and current.status == "running":
                current.result, current.status, current.finished_at = result, "completed", now()
                current.error, current.lease_until = None, None
    except Exception as exc:
        log.exception("Inference job %s failed", job_id)
        with Session.begin() as db:
            current = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if current.attempts == attempt and current.status == "running":
                permanent = isinstance(exc, InferenceUnavailable)
                current.status = "queued" if attempt < 3 and not permanent else "failed"
                current.error = (
                    str(exc)
                    if permanent
                    else "Không xử lý được ảnh/job; kiểm tra storage và worker log"
                )
                current.lease_until = None
                if current.status == "failed":
                    current.finished_at = now()
    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    next_weather_poll = 0
    while True:
        try:
            if time.monotonic() >= next_weather_poll:
                next_weather_poll = time.monotonic() + 60
                collect_weather()
                try:
                    with Session.begin() as db:
                        enqueue_live_forecast(db)
                except InferenceUnavailable:
                    pass  # DEMO/thiếu lịch sử/ngoài cửa sổ; vẫn tiếp tục xử lý ảnh.
            run_once()
        except Exception:
            log.exception("Worker iteration failed")
        time.sleep(1)
