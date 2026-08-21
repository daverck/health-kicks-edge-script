#!/usr/bin/env python3
"""Bridge between an Arduino serial connection and a local MQTT broker."""

from __future__ import annotations

import json
import logging
import os
import queue
import signal
import threading
from typing import Any

import paho.mqtt.client as mqtt
import serial
from serial import SerialException


SERIAL_DEVICE = os.getenv("EDGE_SERIAL_DEVICE", "/dev/ttyUSB0")
SERIAL_BAUDRATE = int(os.getenv("EDGE_SERIAL_BAUDRATE", "115200"))
MQTT_HOST = os.getenv("EDGE_MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("EDGE_MQTT_PORT", "1883"))
TELEMETRY_TOPIC = os.getenv(
    "EDGE_TELEMETRY_TOPIC", "healthkicks/telemetry/raw"
)
COMMAND_TOPIC = os.getenv("EDGE_COMMAND_TOPIC", "healthkicks/commands/haptic")

LOGGER = logging.getLogger("edge_bridge")


def normalize_command(payload: bytes) -> bytes:
    """Return a serial command, ensuring the Arduino receives a line ending."""
    command = payload.rstrip(b"\r\n")
    return command + b"\n"


class SerialBridge:
    """Maintain the serial connection and bridge data in both directions."""

    def __init__(self, stop_event: threading.Event) -> None:
        self._mqtt_client: mqtt.Client | None = None
        self._stop_event = stop_event
        self._commands: queue.Queue[bytes] = queue.Queue(maxsize=100)
        self._serial: serial.Serial | None = None

    def set_mqtt_client(self, mqtt_client: mqtt.Client) -> None:
        self._mqtt_client = mqtt_client

    def enqueue_command(self, payload: bytes) -> None:
        command = normalize_command(payload)
        try:
            self._commands.put_nowait(command)
        except queue.Full:
            LOGGER.warning("Command queue full; dropping MQTT command")

    def run(self) -> None:
        reconnect_delay = 1.0
        while not self._stop_event.is_set():
            if self._serial is None:
                try:
                    self._serial = serial.Serial(
                        port=SERIAL_DEVICE,
                        baudrate=SERIAL_BAUDRATE,
                        timeout=1.0,
                        write_timeout=1.0,
                    )
                    reconnect_delay = 1.0
                    LOGGER.info("Serial connection opened: %s", SERIAL_DEVICE)
                except (SerialException, OSError) as error:
                    LOGGER.warning(
                        "Serial connection failed (%s); retrying in %.1fs",
                        error,
                        reconnect_delay,
                    )
                    self._stop_event.wait(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 2, 30.0)
                    continue

            try:
                self._write_pending_commands()
                line = self._serial.readline()
                if line:
                    self._publish_json(line)
            except (SerialException, OSError) as error:
                LOGGER.warning("Serial connection lost: %s", error)
                self._close_serial()

        self._close_serial()

    def _write_pending_commands(self) -> None:
        if self._serial is None:
            return
        try:
            while True:
                command = self._commands.get_nowait()
                self._serial.write(command)
                self._serial.flush()
                LOGGER.debug("Haptic command sent to Arduino")
        except queue.Empty:
            return

    def _publish_json(self, line: bytes) -> None:
        payload = line.strip()
        try:
            json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            LOGGER.warning("Ignoring invalid serial JSON: %s", error)
            return

        if self._mqtt_client is None:
            LOGGER.warning("MQTT client is not initialized; dropping telemetry")
            return
        result = self._mqtt_client.publish(TELEMETRY_TOPIC, payload=payload, qos=0)
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            LOGGER.warning("MQTT telemetry publish failed: %s", result.rc)

    def _close_serial(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            except OSError:
                LOGGER.debug("Error while closing serial connection", exc_info=True)
            finally:
                self._serial = None


def build_mqtt_client(serial_bridge: SerialBridge) -> mqtt.Client:
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=os.getenv("EDGE_MQTT_CLIENT_ID", "smartstride-edge"),
    )

    username = os.getenv("EDGE_MQTT_USERNAME")
    password = os.getenv("EDGE_MQTT_PASSWORD")
    if username:
        client.username_pw_set(username, password)

    def on_connect(
        mqtt_client: mqtt.Client,
        _: Any,
        __: dict[str, Any],
        reason_code: mqtt.ReasonCode,
        ___: Any,
    ) -> None:
        if reason_code == 0:
            mqtt_client.subscribe(COMMAND_TOPIC, qos=0)
            LOGGER.info("Connected to MQTT broker %s:%s", MQTT_HOST, MQTT_PORT)
        else:
            LOGGER.warning("MQTT connection refused: %s", reason_code)

    def on_message(
        _: mqtt.Client, __: Any, message: mqtt.MQTTMessage
    ) -> None:
        serial_bridge.enqueue_command(message.payload)

    client.on_connect = on_connect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    return client


def main() -> None:
    logging.basicConfig(
        level=os.getenv("EDGE_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    stop_event = threading.Event()

    serial_bridge = SerialBridge(stop_event)
    mqtt_client = build_mqtt_client(serial_bridge)
    serial_bridge.set_mqtt_client(mqtt_client)

    def request_shutdown(signum: int, _: Any) -> None:
        LOGGER.info("Received signal %s; shutting down", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)

    serial_thread = threading.Thread(
        target=serial_bridge.run, name="serial-bridge", daemon=True
    )
    serial_thread.start()

    try:
        mqtt_client.connect_async(MQTT_HOST, MQTT_PORT, keepalive=60)
        mqtt_client.loop_start()
        while not stop_event.wait(1.0):
            pass
    except (OSError, mqtt.MQTTException):
        LOGGER.exception("MQTT bridge stopped unexpectedly")
        raise
    finally:
        stop_event.set()
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
        serial_thread.join(timeout=3.0)


if __name__ == "__main__":
    main()