import asyncio
import contextlib
import logging
import time
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from app.api.routes import dashboard, router, valid_token
from app.core.schemas import DEVICE_ID
from app.core.settings import settings
from app.db.models import Device, Policy, now
from app.db.session import Session, engine
from app.integrations.mqtt import bridge
from app.modules.control import maintain
from app.modules.stream_relay import stream_relay

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


async def scheduler():
    while True:
        try:
            await run_in_threadpool(maintain, bridge)
        except Exception:
            log.exception("Control scheduler failed")
        await asyncio.sleep(2)


@asynccontextmanager
async def lifespan(app):
    # The demo uses one API process: do not duplicate MQTT/scheduler consumers.
    lock = engine.connect()
    if not lock.scalar(text("SELECT pg_try_advisory_lock(73419201)")):
        lock.close()
        raise RuntimeError("Only one backend instance may run for this demo database")
    lock.commit()  # The session advisory lock survives commit; avoid a long idle transaction.
    with Session.begin() as db:
        if not db.get(Device, DEVICE_ID):
            db.add(Device(id=DEVICE_ID))
        if not db.get(Policy, 1):
            db.add(Policy(id=1))
    bridge.start()
    task = asyncio.create_task(scheduler())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        bridge.stop()
        lock.execute(text("SELECT pg_advisory_unlock(73419201)"))
        lock.close()


app = FastAPI(title="AIoT Plant Care • Simulation API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins.split(","),
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["Authorization", "Content-Type"],
)
app.include_router(router)


@app.exception_handler(SQLAlchemyError)
async def database_unavailable(request, exc):
    log.error("Database request failed: %s", type(exc).__name__)
    return JSONResponse(
        status_code=503,
        content={
            "detail": "Cơ sở dữ liệu chưa sẵn sàng. Kiểm tra Docker và thử lại.",
        },
    )


@app.get("/health")
def health():
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return {
        "status": "ok",
        "source": "simulation",
        "ai_provider": "trained",
        "mqtt_connected": bridge.connected,
    }


@app.websocket("/ws")
async def realtime(websocket: WebSocket):
    origin = websocket.headers.get("origin")
    if origin and origin not in settings.cors_origins.split(","):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    try:
        # Token is sent in first frame, never in a URL/access log.
        auth = await asyncio.wait_for(websocket.receive_json(), timeout=5)
        if not isinstance(auth, dict) or not valid_token(auth.get("token")):
            await websocket.close(code=1008)
            return
        while True:
            snapshot = await run_in_threadpool(dashboard)
            await websocket.send_json(
                {
                    "type": "snapshot",
                    "event_id": str(uuid4()),
                    "timestamp": now().isoformat(),
                    "data": snapshot,
                }
            )
            await asyncio.sleep(2)
    except SQLAlchemyError:
        with contextlib.suppress(RuntimeError, WebSocketDisconnect):
            await websocket.send_json({"type": "service_unavailable"})
            await websocket.close(code=1013)
    except (WebSocketDisconnect, TimeoutError, RuntimeError, ValueError):
        return


@app.websocket("/ws/stream")
async def websocket_stream(websocket: WebSocket):
    origin = websocket.headers.get("origin")
    if origin and origin not in settings.cors_origins.split(","):
        await websocket.close(code=1008)
        return

    token = websocket.query_params.get("token")
    cam_url = websocket.query_params.get("cam_url")

    await websocket.accept()

    try:
        # Xác thực: nếu không có trong query params, đợi bản tin JSON đầu tiên
        if not token or not valid_token(token):
            auth = await asyncio.wait_for(websocket.receive_json(), timeout=5)
            if not isinstance(auth, dict) or not valid_token(auth.get("token")):
                await websocket.close(code=1008)
                return
            if not cam_url and auth.get("cam_url"):
                cam_url = auth.get("cam_url")

        if cam_url:
            stream_relay.set_cam_url(cam_url)

        stream_relay.add_viewer()
        last_sent_seq = -1
        last_sent_ai_time = -1.0

        async def reader_loop():
            try:
                while True:
                    data = await websocket.receive_json()
                    if isinstance(data, dict):
                        if data.get("type") == "set_cam_url" and data.get("cam_url"):
                            stream_relay.set_cam_url(data["cam_url"])
                        elif data.get("type") == "ping":
                            await websocket.send_json({"type": "pong", "timestamp": time.time()})
            except Exception:
                pass

        reader_task = asyncio.create_task(reader_loop())

        try:
            while not reader_task.done():
                raw_jpeg, seq, _ = stream_relay.get_latest_frame()
                if raw_jpeg and seq != last_sent_seq:
                    await websocket.send_bytes(raw_jpeg)
                    last_sent_seq = seq

                dets, leaf, ai_time = stream_relay.get_latest_ai_metadata()
                if ai_time > 0 and ai_time != last_sent_ai_time:
                    await websocket.send_json(
                        {
                            "type": "ai_overlay",
                            "leaf": leaf,
                            "fruits": dets,
                            "timestamp": ai_time,
                        }
                    )
                    last_sent_ai_time = ai_time

                await asyncio.sleep(0.01)  # Tần số quét 100Hz (~10ms) đảm bảo frame gửi tức thì, triệt tiêu jitter
        finally:
            reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reader_task
    except (WebSocketDisconnect, TimeoutError, RuntimeError, ValueError):
        pass
    finally:
        stream_relay.remove_viewer()

