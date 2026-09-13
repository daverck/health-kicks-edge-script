"""BlueZ GATT Server implementation using bluezero for HealthKicks Footwear.
Contract Reference: contracts/ble_gatt_specs.md
"""
from __future__ import annotations

import logging
import struct
import threading
import time
from typing import Callable

from healthkicks_edge.schemas import HapticCommand, StudioCaptureConfig
from healthkicks_edge.transport.ble.constants import (
    CHAR_ACTIVITY_DETECTION_UUID,
    CHAR_HAPTIC_COMMAND_UUID,
    CHAR_STUDIO_CONTROL_UUID,
    CHAR_STUDIO_DATA_BURST_UUID,
    EVENT_TYPE_TO_STATE_CODE,
    FOOTWEAR_SERVICE_UUID,
    STATE_CODE_IDLE,
)

LOGGER = logging.getLogger(__name__)

# Safe import of bluezero (only available on Linux with D-Bus/BlueZ)
try:
    from bluezero import adapter, peripheral
    HAS_BLUEZERO = True
except (ImportError, Exception) as err:
    LOGGER.debug("bluezero_not_available reason=%s", err)
    HAS_BLUEZERO = False
    peripheral = None
    adapter = None


class HealthKicksGattServer:
    """Manages the HealthKicks GATT peripheral server and BlueZ registration."""

    def __init__(
        self,
        device_name: str = "HealthKicks-HK-1",
        adapter_name: str = "hci0",
        on_haptic_command: Callable[[HapticCommand], None] | None = None,
        on_studio_command: Callable[[StudioCaptureConfig], bool] | None = None,
        on_studio_cancel: Callable[[], None] | None = None,
    ) -> None:
        self.device_name = device_name
        self.adapter_name = adapter_name
        self._on_haptic_command = on_haptic_command
        self._on_studio_command = on_studio_command
        self._on_studio_cancel = on_studio_cancel

        self._lock = threading.Lock()
        self._is_running = False
        self._peripheral_app = None
        self._thread: threading.Thread | None = None

        # State storage
        self._last_detection_bytes: bytes = struct.pack(">BBI B", STATE_CODE_IDLE, 0, 0, 0)
        self._activity_char_obj = None
        self._studio_control_char_obj = None
        self._studio_burst_char_obj = None

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._is_running

    def set_callbacks(
        self,
        on_haptic_command: Callable[[HapticCommand], None] | None = None,
        on_studio_command: Callable[[StudioCaptureConfig], bool] | None = None,
        on_studio_cancel: Callable[[], None] | None = None,
    ) -> None:
        if on_haptic_command:
            self._on_haptic_command = on_haptic_command
        if on_studio_command:
            self._on_studio_command = on_studio_command
        if on_studio_cancel:
            self._on_studio_cancel = on_studio_cancel

    def build_peripheral(self) -> Any:
        """Construct the Peripheral GATT hierarchy with the HealthKicks Service."""
        if not HAS_BLUEZERO:
            raise RuntimeError(
                "bluezero is required for BLE GATT Server on Linux. "
                "Ensure bluezero and D-Bus/BlueZ are installed and active."
            )

        # Check adapter availability
        try:
            available_adapters = list(adapter.Adapter.available())
            adapter_address = None
            for a in available_adapters:
                if a.address or self.adapter_name in str(a):
                    adapter_address = a.address
                    break
            if not adapter_address and available_adapters:
                adapter_address = available_adapters[0].address
        except Exception as ex:
            LOGGER.warning("could_not_query_adapters error=%s, using fallback", ex)
            adapter_address = None

        app = peripheral.Peripheral(
            adapter_address=adapter_address,
            local_name=self.device_name,
            appearance=0x0440,  # Generic Running Walking Sensor
        )

        # 1. Primary Service
        app.add_service(
            srv_id=1,
            uuid=FOOTWEAR_SERVICE_UUID,
            primary=True,
        )

        # 2. Characteristic 1 : Activity Detection (READ, NOTIFY)
        app.add_characteristic(
            srv_id=1,
            chr_id=1,
            uuid=CHAR_ACTIVITY_DETECTION_UUID,
            value=list(self._last_detection_bytes),
            notifying=False,
            flags=["read", "notify"],
            read_callback=self._handle_read_activity,
            notify_callback=self._handle_notify_activity_status,
        )

        # 3. Characteristic 2 : Haptic Command (WRITE, WRITE WITHOUT RESPONSE)
        app.add_characteristic(
            srv_id=1,
            chr_id=2,
            uuid=CHAR_HAPTIC_COMMAND_UUID,
            value=[],
            notifying=False,
            flags=["write", "write-without-response"],
            write_callback=self._handle_write_haptic,
        )

        # 4. Characteristic 3 : Studio Control (WRITE, NOTIFY)
        app.add_characteristic(
            srv_id=1,
            chr_id=3,
            uuid=CHAR_STUDIO_CONTROL_UUID,
            value=list(b"IDLE"),
            notifying=False,
            flags=["write", "notify"],
            write_callback=self._handle_write_studio_control,
            notify_callback=self._handle_notify_studio_status,
        )

        # 5. Characteristic 4 : Studio Data Burst (NOTIFY)
        app.add_characteristic(
            srv_id=1,
            chr_id=4,
            uuid=CHAR_STUDIO_DATA_BURST_UUID,
            value=[],
            notifying=False,
            flags=["notify"],
            notify_callback=self._handle_notify_burst_status,
        )

        return app

    def start(self) -> None:
        """Starts the GATT server in a background event-loop thread."""
        with self._lock:
            if self._is_running:
                return
            self._is_running = True

        if not HAS_BLUEZERO:
            LOGGER.warning("bluezero_not_installed_starting_dummy_gatt_server")
            return

        try:
            self._peripheral_app = self.build_peripheral()
            self._thread = threading.Thread(
                target=self._run_loop,
                name="gatt-server-event-loop",
                daemon=True,
            )
            self._thread.start()
            LOGGER.info("ble_gatt_server_started device_name=%s", self.device_name)
        except Exception as err:
            LOGGER.error("failed_to_start_ble_gatt_server error=%s", err)
            with self._lock:
                self._is_running = False
            raise

    def _run_loop(self) -> None:
        try:
            if self._peripheral_app:
                self._peripheral_app.publish()
        except Exception as err:
            LOGGER.error("ble_gatt_loop_error error=%s", err)
        finally:
            with self._lock:
                self._is_running = False

    def stop(self) -> None:
        """Stops the GATT peripheral and disconnects."""
        with self._lock:
            if not self._is_running:
                return
            self._is_running = False

        if self._peripheral_app:
            try:
                # Disconnect and unpublish peripheral
                if hasattr(self._peripheral_app, "quit"):
                    self._peripheral_app.quit()
            except Exception as err:
                LOGGER.debug("error_stopping_peripheral err=%s", err)

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        LOGGER.info("ble_gatt_server_stopped")

    # --- Callbacks for Characteristics ---

    def _handle_read_activity(self) -> list[int]:
        with self._lock:
            return list(self._last_detection_bytes)

    def _handle_notify_activity_status(self, notifying: bool, char_obj: Any = None) -> None:
        LOGGER.debug("activity_notify_subscription_changed active=%s", notifying)
        if char_obj:
            self._activity_char_obj = char_obj

    def _handle_write_haptic(self, value: Sequence[int], options: dict[str, Any] | None = None) -> None:
        """Parses Haptic Command bytes per contracts/ble_gatt_specs.md:
        [pattern_id (1B), intensity (1B: 0..255), duration_ms (2B: uint16 big-endian)]
        """
        raw_bytes = bytes(value)
        LOGGER.debug("gatt_write_haptic raw_bytes=%s", raw_bytes.hex())

        if len(raw_bytes) >= 4:
            pattern_id, intensity, duration_ms = struct.unpack(">BBH", raw_bytes[:4])
        elif len(raw_bytes) == 2:
            intensity, duration_ms = raw_bytes[0], raw_bytes[1] * 10
        elif len(raw_bytes) == 3:
            intensity = raw_bytes[0]
            duration_ms = struct.unpack(">H", raw_bytes[1:3])[0]
        else:
            LOGGER.warning("invalid_haptic_payload_length len=%d", len(raw_bytes))
            return

        intensity = max(0, min(255, intensity))
        duration_ms = max(50, min(10000, duration_ms))

        if self._on_haptic_command:
            cmd = HapticCommand(intensity=intensity, duration_ms=duration_ms)
            self._on_haptic_command(cmd)

    def _handle_write_studio_control(self, value: Sequence[int], options: dict[str, Any] | None = None) -> None:
        """Parses Studio Control commands per contracts/ble_gatt_specs.md:
        START <label> <duration_sec> <session_id>
        CANCEL
        """
        try:
            text = bytes(value).decode("utf-8").strip()
        except UnicodeDecodeError:
            LOGGER.warning("invalid_utf8_studio_command")
            self.notify_studio_status("ERROR invalid_encoding")
            return

        LOGGER.info("gatt_studio_control_write command=%s", text)
        parts = text.split()
        if not parts:
            return

        action = parts[0].upper()
        if action == "START":
            if len(parts) < 4:
                LOGGER.warning("invalid_start_args parts=%s", parts)
                self.notify_studio_status("ERROR invalid_arguments")
                return
            label = parts[1]
            try:
                duration_sec = float(parts[2])
            except ValueError:
                self.notify_studio_status("ERROR invalid_duration")
                return
            session_id = parts[3]

            config = StudioCaptureConfig(
                session_id=session_id,
                label=label,
                duration_sec=duration_sec,
            )
            if self._on_studio_command:
                accepted = self._on_studio_command(config)
                if not accepted:
                    self.notify_studio_status("ERROR busy")
        elif action in ("CANCEL", "STOP"):
            if self._on_studio_cancel:
                self._on_studio_cancel()
            self.notify_studio_status("CANCELLED")
        else:
            LOGGER.warning("unknown_studio_action action=%s", action)
            self.notify_studio_status("ERROR unknown_action")

    def _handle_notify_studio_status(self, notifying: bool, char_obj: Any = None) -> None:
        if char_obj:
            self._studio_control_char_obj = char_obj

    def _handle_notify_burst_status(self, notifying: bool, char_obj: Any = None) -> None:
        if char_obj:
            self._studio_burst_char_obj = char_obj

    # --- Notification Emitters ---

    def notify_detection(self, event_type: str, confidence: float, timestamp_sec: int) -> None:
        """Format 7-byte binary notification and emit on Activity Detection characteristic."""
        state_code = EVENT_TYPE_TO_STATE_CODE.get(event_type.lower(), STATE_CODE_IDLE)
        conf_byte = max(0, min(100, int(round(confidence * 100.0))))
        ts_uint32 = max(0, int(timestamp_sec))
        flags = 0x01 if "fall" in event_type.lower() else 0x00

        data = struct.pack(">BBI B", state_code, conf_byte, ts_uint32, flags)
        with self._lock:
            self._last_detection_bytes = data

        if self._activity_char_obj and hasattr(self._activity_char_obj, "set_value"):
            try:
                self._activity_char_obj.set_value(list(data))
            except Exception as ex:
                LOGGER.debug("failed_to_notify_detection err=%s", ex)

    def notify_studio_status(self, status_msg: str) -> None:
        """Emit text status notification on Studio Control characteristic."""
        raw = list(status_msg.encode("utf-8"))
        if self._studio_control_char_obj and hasattr(self._studio_control_char_obj, "set_value"):
            try:
                self._studio_control_char_obj.set_value(raw)
            except Exception as ex:
                LOGGER.debug("failed_to_notify_studio_status err=%s", ex)

    def send_burst_packets(self, packets: Sequence[bytes], inter_packet_delay_sec: float = 0.01) -> int:
        """Sequentially notify burst packets on Studio Data Burst characteristic."""
        count = 0
        for pkt in packets:
            if self._studio_burst_char_obj and hasattr(self._studio_burst_char_obj, "set_value"):
                try:
                    self._studio_burst_char_obj.set_value(list(pkt))
                    count += 1
                except Exception as ex:
                    LOGGER.warning("failed_to_send_burst_packet seq=%d err=%s", count, ex)
            else:
                # Simulated / in-memory count
                count += 1
            if inter_packet_delay_sec > 0:
                time.sleep(inter_packet_delay_sec)
        return count
