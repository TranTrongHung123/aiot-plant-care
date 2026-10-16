import json
import logging

import paho.mqtt.client as mqtt
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.core.schemas import DEVICE_ID, PREFIX, Ack, Telemetry, fresh
from app.core.settings import settings
from app.db.models import Command, Device, Reading, now
from app.db.session import Session
from app.modules.control import event

log = logging.getLogger(__name__)


class Bridge:
    def __init__(self):
        self.connected = False
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id="plantcare-demo-backend"
        )
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_message = self.on_message
        self.client.reconnect_delay_set(1, 10)

    def on_connect(self, client, userdata, flags, reason, properties):
        self.connected = not reason.is_failure
        if self.connected:
            for suffix in ("telemetry", "status", "ack"):
                client.subscribe(f"{PREFIX}/{DEVICE_ID}/{suffix}", qos=1)

    def on_disconnect(self, client, userdata, flags, reason, properties):
        self.connected = False

    def start(self):
        self.client.connect_async(settings.demo_mqtt_host, settings.demo_mqtt_port, 20)
        self.client.loop_start()

    def stop(self):
        self.client.disconnect()
        self.client.loop_stop()

    def publish(self, suffix, payload):
        if not self.connected:
            raise RuntimeError("MQTT disconnected")
        info = self.client.publish(
            f"{PREFIX}/{DEVICE_ID}/{suffix}", json.dumps(payload), qos=1, retain=False
        )
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError("MQTT publish failed")

    def on_message(self, client, userdata, message):
        try:
            self.ingest(message.topic, message.payload, message.retain)
        except (ValidationError, ValueError, KeyError, TypeError):
            log.warning("Rejected invalid MQTT message on %s", message.topic)
        except Exception:
            log.exception("MQTT ingestion failed")

    def ingest(self, topic, payload, retained=False):
        if len(payload) > 16_384 or not topic.startswith(f"{PREFIX}/{DEVICE_ID}/"):
            return
        data = json.loads(payload)
        suffix = topic.rsplit("/", 1)[1]
        if data.get("source") != "simulation" or data.get("device_id") != DEVICE_ID:
            return
        if suffix == "telemetry":
            item = Telemetry.model_validate(data)
            if retained or not fresh(item.measured_at, now(), 300):
                return
            with Session.begin() as db:
                db.execute(
                    insert(Reading)
                    .values(
                        device_id=DEVICE_ID,
                        message_id=str(item.message_id),
                        measured_at=item.measured_at,
                        readings=item.readings.model_dump(),
                    )
                    .on_conflict_do_nothing(constraint="sensor_readings_device_id_message_id_key")
                )
        elif suffix == "status":
            from datetime import datetime

            timestamp = datetime.fromisoformat(data["timestamp"])
            if timestamp.tzinfo is None or not fresh(timestamp, now()):
                return
            if data.get("pump") not in ("on", "off", "unknown"):
                return
            if not isinstance(data.get("online"), bool):
                return
            with Session.begin() as db:
                device = db.get(Device, DEVICE_ID)
                if device.last_seen and timestamp < device.last_seen:
                    return
                device.online = data["online"]
                device.pump = data["pump"]
                device.last_seen = timestamp
                device.scenario = data.get("scenario", "normal")
        elif suffix == "ack" and not retained:
            item = Ack.model_validate(data)
            with Session.begin() as db:
                command = db.scalar(
                    select(Command).where(Command.id == str(item.command_id)).with_for_update()
                )
                if not command or command.device_id != item.device_id:
                    return
                if command.status in ("completed", "rejected", "failed"):
                    return
                if item.status == "accepted" and command.status in ("accepted", "timed_out"):
                    return
                event(command, item.status, item.reason)


bridge = Bridge()
