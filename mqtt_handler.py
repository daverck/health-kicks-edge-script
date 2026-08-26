from __future__ import annotations

import json
import logging
from pathlib import Path
import threading
import time
from datetime import datetime, timezone
from typing import Callable

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion
from pydantic import ValidationError

from schemas import DeviceStatus, DeviceStatusPayload, FallEvent, Header, HapticCommand, Telemetry

LOGGER = logging.getLogger(__name__)


class MQTTHandler:
    def __init__(
        self,
        host: str,
        port: int,
        client_id: str,
        username: str | None,
        password: str | None,
        device_id: str,
        telemetry_topic: str,
        fall_topic: str,
        command_topic: str,
        status_topic: str,
        ack_topic: str,
        heartbeat_interval: int,
        on_haptic_command: Callable[[HapticCommand], None],
    ) -> None:
        self._device_id = device_id
        self._fall_topic = fall_topic
        self._telemetry_topic = telemetry_topic
        self._command_topic = command_topic
        self._status_topic = status_topic
        self._ack_topic = ack_topic
        self._heartbeat_interval = heartbeat_interval
        self._on_haptic_command = on_haptic_command
        self._started_at = time.monotonic()
        self._stop_event = threading.Event()
        self.client = mqtt.Client(callback_api_version=CallbackAPIVersion.VERSION2, client_id=client_id)
        if username:
            self.client.username_pw_set(username, password)
        self.client.will_set(
            status_topic,
            '{"state":"offline","reason":"unexpected_disconnection"}',
            qos=1,
            retain=True,
        )
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        self._host = host
        self._port = port

    def start(self) -> None:
        self.client.connect_async(self._host, self._port, keepalive=60)
        self.client.loop_start()
        self._publish_status("online")

    def stop(self) -> None:
        self._stop_event.set()
        self.client.loop_stop()
        self.client.disconnect()

    def publish_telemetry(self, telemetry: Telemetry) -> None:
        self._publish(self._telemetry_topic, telemetry.model_dump_json(), qos=0)

    def publish_fall(self, event: FallEvent) -> None:
        self._publish(self._fall_topic, event.model_dump_json(), qos=1)

    def publish_arduino_response(self, response: str) -> None:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": response,
        }
        self._publish(self._ack_topic, json.dumps(payload), qos=1)

    def heartbeat_loop(self) -> None:
        while not self._stop_event.wait(self._heartbeat_interval):
            self._publish_status("online")

    def _on_connect(self, client: mqtt.Client, _: object, __: dict, reason_code: mqtt.ReasonCode, ___: object) -> None:
        if reason_code == 0:
            client.subscribe(self._command_topic, qos=1)
            LOGGER.info("mqtt_connected host=%s port=%s", self._host, self._port)
        else:
            LOGGER.warning("mqtt_connection_refused reason=%s", reason_code)

    def _on_message(self, _: mqtt.Client, __: object, message: mqtt.MQTTMessage) -> None:
        try:
            command = HapticCommand.model_validate_json(message.payload)
        except (ValidationError, ValueError) as error:
            LOGGER.warning("mqtt_command_invalid error=%s", error)
            return
        self._on_haptic_command(command)

    def _publish_status(self, state: str, reason: str | None = None) -> None:
        cpu_temp = self._cpu_temperature()
        status = self._status(
            state,
            reason=reason,
            uptime=int(time.monotonic() - self._started_at),
            cpu_temp=cpu_temp,
        )
        self._publish(self._status_topic, status.model_dump_json(), qos=1, retain=True)

    def _status(
        self,
        state: str,
        reason: str | None = None,
        uptime: int | None = None,
        cpu_temp: float | None = None,
    ) -> DeviceStatus:
        return DeviceStatus(
            header=Header(device_id=self._device_id, timestamp=datetime.now(timezone.utc)),
            payload=DeviceStatusPayload(
                state=state, reason=reason, uptime=uptime, cpu_temp=cpu_temp
            ),
        )

    @staticmethod
    def _cpu_temperature() -> float | None:
        for path in Path("/sys/class/thermal").glob("thermal_zone*/temp"):
            try:
                return int(path.read_text().strip()) / 1000
            except (OSError, ValueError):
                continue
        return None

    def _publish(self, topic: str, payload: str, qos: int, retain: bool = False) -> None:
        result = self.client.publish(topic, payload=payload, qos=qos, retain=retain)
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            LOGGER.warning("mqtt_publish_failed topic=%s rc=%s", topic, result.rc)
