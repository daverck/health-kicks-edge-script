from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable

import serial
from serial import SerialException

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class QueuedCommand:
    payload: bytes
    expires_at: float


class SerialHandler:
    def __init__(
        self,
        device: str,
        baudrate: int,
        stop_event: threading.Event,
        command_ttl: float,
        on_data: Callable[[dict[str, float]], None],
        on_response: Callable[[str], None] | None = None,
    ) -> None:
        self._device = device
        self._baudrate = baudrate
        self._stop_event = stop_event
        self._command_ttl = command_ttl
        self._on_data = on_data
        self._on_response = on_response
        self._commands: queue.Queue[QueuedCommand] = queue.Queue(maxsize=100)
        self._serial: serial.Serial | None = None

    def enqueue_haptic(self, intensity: int, duration_ms: int) -> None:
        command = QueuedCommand(
            payload=f"CMD:VIB:{intensity}:{duration_ms}\n".encode("ascii"),
            expires_at=time.monotonic() + self._command_ttl,
        )
        try:
            self._commands.put_nowait(command)
        except queue.Full:
            LOGGER.warning("serial_command_dropped reason=queue_full")

    def run(self) -> None:
        retry_delay = 1.0
        while not self._stop_event.is_set():
            if self._serial is None:
                try:
                    self._serial = serial.Serial(
                        port=self._device,
                        baudrate=self._baudrate,
                        timeout=1.0,
                        write_timeout=1.0,
                    )
                    retry_delay = 1.0
                    LOGGER.info("serial_connected device=%s", self._device)
                except (SerialException, OSError) as error:
                    LOGGER.warning("serial_connect_failed error=%s retry=%.1f", error, retry_delay)
                    self._stop_event.wait(retry_delay)
                    retry_delay = min(retry_delay * 2, 30.0)
                    continue

            try:
                self._write_pending()
                line = self._serial.readline()
                if line:
                    self._handle_line(line)
            except (SerialException, OSError) as error:
                LOGGER.warning("serial_disconnected error=%s", error)
                self._close()
        self._close()

    def _write_pending(self) -> None:
        if self._serial is None:
            return
        while True:
            try:
                command = self._commands.get_nowait()
            except queue.Empty:
                return
            if command.expires_at <= time.monotonic():
                LOGGER.warning("serial_command_expired")
                continue
            self._serial.write(command.payload)
            self._serial.flush()
            LOGGER.info("serial_command_sent command=%s", command.payload.decode().strip())

    def _handle_line(self, line: bytes) -> None:
        text = line.decode("utf-8", errors="replace").strip()
        if not text:
            return

        if text.startswith("ACK:") or text.startswith("ERR:"):
            LOGGER.info("arduino_response response=%s", text)
            if self._on_response is not None:
                self._on_response(text)
            return

        if not text.startswith("DATA:"):
            LOGGER.warning("serial_line_ignored raw_line=%s", text)
            return

        import json

        # Découpe proprement au premier ':' pour récupérer tout le JSON après 'DATA:'
        _, _, payload_str = text.partition(":")
        payload_str = payload_str.strip()

        try:
            data = json.loads(payload_str)
            if not isinstance(data, dict):
                raise ValueError("IMU payload must be an object")
            axes = {axis: float(data[axis]) for axis in ("ax", "ay", "az", "gx", "gy", "gz")}
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
            LOGGER.warning("serial_data_invalid error=%s raw_payload=%s", error, payload_str)
            return

        self._on_data(axes)

    def _close(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            except OSError:
                LOGGER.debug("serial_close_failed", exc_info=True)
            finally:
                self._serial = None
