"""A local MQTT device simulator; can only use the simulation namespace."""

import json
import math
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

import paho.mqtt.client as mqtt

from app.core.schemas import DEVICE_ID, PREFIX
from app.core.settings import settings
from app.db.models import now


class Simulator:
    def __init__(self, state_path: Path | None = None):
        self.lock = threading.RLock()
        self.scenario, self.active, self.deadline = "normal", None, 0
        self.boot_id, self.sequence = str(uuid4()), 0
        state = state_path or Path(__file__).resolve().parents[2] / ".simulator.sqlite3"
        self.db = sqlite3.connect(state, check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS commands (id TEXT PRIMARY KEY, ack TEXT)")
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id="plantcare-demo-simulator"
        )
        self.client.on_connect = self.connected
        self.client.on_message = self.message
        self.client.on_disconnect = self.disconnected
        self.client.will_set(
            f"{PREFIX}/{DEVICE_ID}/status",
            json.dumps(
                {
                    "schema_version": 1,
                    "device_id": DEVICE_ID,
                    "source": "simulation",
                    "timestamp": now().isoformat(),
                    "online": False,
                    "pump": "unknown",
                }
            ),
            qos=1,
            retain=True,
        )

    def publish(self, suffix, data, retain=False):
        self.client.publish(
            f"{PREFIX}/{DEVICE_ID}/{suffix}",
            json.dumps(
                {
                    "schema_version": 1,
                    "device_id": DEVICE_ID,
                    "source": "simulation",
                    **data,
                }
            ),
            qos=1,
            retain=retain,
        )

    def status(self):
        self.publish(
            "status",
            {
                "timestamp": now().isoformat(),
                "online": self.scenario != "offline",
                "pump": "on" if self.active else "off",
                "scenario": self.scenario,
            },
            retain=True,
        )

    def ack(self, command_id, status, reason):
        data = {
            "command_id": command_id,
            "timestamp": now().isoformat(),
            "status": status,
            "reason": reason,
        }
        self.db.execute(
            "INSERT OR REPLACE INTO commands VALUES (?, ?)", (command_id, json.dumps(data))
        )
        self.db.commit()
        self.publish("ack", data)

    def connected(self, client, userdata, flags, reason, properties):
        if not reason.is_failure:
            client.subscribe(f"{PREFIX}/{DEVICE_ID}/command", qos=1)
            client.subscribe(f"{PREFIX}/{DEVICE_ID}/simulation-config", qos=1)
            with self.lock:
                # A reboot never resumes the previous pump command.
                for command_id, payload in self.db.execute(
                    "SELECT id, ack FROM commands"
                ).fetchall():
                    if json.loads(payload)["status"] == "accepted" and command_id != self.active:
                        self.ack(command_id, "failed", "Simulator khởi động lại; bơm OFF")
                self.status()

    def disconnected(self, client, userdata, flags, reason, properties):
        with self.lock:
            self.active = None  # Local fail-safe, never wait for a remote stop.

    def message(self, client, userdata, message):
        with self.lock:
            try:
                if len(message.payload) > 16_384 or message.retain:
                    return
                data = json.loads(message.payload)
                if not isinstance(data, dict):
                    return
                if message.topic == f"{PREFIX}/{DEVICE_ID}/simulation-config":
                    if data.get("scenario") in ("normal", "dry", "rain", "offline"):
                        self.scenario = data["scenario"]
                        if self.active and self.scenario == "offline":
                            self.ack(self.active, "failed", "Mất kết nối mô phỏng; bơm OFF")
                            self.active = None
                        self.status()
                    return
                if self.scenario == "offline":
                    return
                if message.topic != f"{PREFIX}/{DEVICE_ID}/command":
                    return
                if data.get("schema_version") != 1:
                    return
                if data.get("source") != "simulation" or data.get("device_id") != DEVICE_ID:
                    return
                if not isinstance(data.get("command_id"), str):
                    return
                command_id = str(UUID(data["command_id"]))
                prior = self.db.execute(
                    "SELECT ack FROM commands WHERE id=?", (command_id,)
                ).fetchone()
                if prior:
                    self.publish("ack", json.loads(prior[0]))
                    return
                expiry = datetime.fromisoformat(data["expires_at"])
                duration = data["duration_ms"]
                if (
                    expiry.tzinfo is None
                    or expiry <= now()
                    or type(duration) is not int
                    or not 1 <= duration <= 15000
                ):
                    self.ack(command_id, "rejected", "Lệnh hết hạn hoặc duration không hợp lệ")
                elif data["action"] == "pump_off":
                    if self.active:
                        self.ack(self.active, "completed", "Đã dừng sớm bằng lệnh OFF")
                    self.active = None
                    self.ack(command_id, "completed", "Bơm đã OFF")
                elif data["action"] == "pump_on" and not self.active:
                    self.active, self.deadline = command_id, time.monotonic() + duration / 1000
                    self.ack(command_id, "accepted", "Relay mô phỏng ON")
                else:
                    self.ack(command_id, "rejected", "Bơm bận hoặc action không hợp lệ")
                self.status()
            except (ValueError, KeyError, TypeError):
                return

    def run(self):
        self.client.connect_async(settings.demo_mqtt_host, settings.demo_mqtt_port, 20)
        self.client.loop_start()
        try:
            while True:
                with self.lock:
                    if self.active and time.monotonic() >= self.deadline:
                        self.ack(self.active, "completed", "Hết thời lượng; relay mô phỏng OFF")
                        self.active = None
                        self.status()
                    if self.sequence % 10 == 0:
                        self.status()
                        if self.scenario != "offline":
                            wave = math.sin(self.sequence / 50)
                            soil = {"normal": 52, "dry": 22, "rain": 70}[self.scenario]
                            self.publish(
                                "telemetry",
                                {
                                    "message_id": str(uuid4()),
                                    "measured_at": now().isoformat(),
                                    "boot_id": self.boot_id,
                                    "sequence": self.sequence,
                                    "readings": {
                                        "temperature_c": round(28 + wave, 1),
                                        "humidity_pct": 88 if self.scenario == "rain" else 62,
                                        "soil_moisture_raw": int(4095 * (1 - soil / 100)),
                                        "soil_moisture_pct": round(soil + wave, 1),
                                        "pressure_hpa": 1005 if self.scenario == "rain" else 1012,
                                    },
                                },
                            )
                    self.sequence += 1
                time.sleep(0.5)
        finally:
            with self.lock:
                self.active, self.scenario = None, "offline"
                self.status()
            self.client.disconnect()
            self.client.loop_stop()
            self.db.close()


if __name__ == "__main__":
    Simulator().run()
