"""BLE GATT Transport implementation for HealthKicks Edge.
Implements the Transport ABC and interfaces with HealthKicksGattServer & BurstPacketizer.
Contract Reference: contracts/ble_gatt_specs.md
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable

from healthkicks_edge.schemas import (
    DetectionEvent,
    HapticCommand,
    StudioCaptureConfig,
    TelemetryBatch,
)
from healthkicks_edge.transport.base import Transport
from healthkicks_edge.transport.ble.burst_packetizer import packetize_readings
from healthkicks_edge.transport.ble.gatt_server import BluetoothAdapterError, HealthKicksGattServer

if TYPE_CHECKING:
    from healthkicks_edge.studio_manager import StudioManager

LOGGER = logging.getLogger(__name__)


class BleTransport(Transport):
    """BLE Transport managing the HealthKicks GATT server and Studio Data Burst transfer."""

    def __init__(
        self,
        device_id: str = "HK-1",
        device_name: str | None = None,
        adapter_name: str = "hci0",
        on_haptic_command: Callable[[HapticCommand], None] | None = None,
        on_studio_command: Callable[[StudioCaptureConfig], bool] | None = None,
        att_mtu: int = 247,
    ) -> None:
        self.device_id = device_id
        self.device_name = device_name or f"HealthKicks-{device_id}"
        self.adapter_name = adapter_name
        self.att_mtu = att_mtu
        self._on_haptic_command = on_haptic_command
        self._on_studio_command = on_studio_command
        self._studio_manager: StudioManager | None = None

        self.gatt_server = HealthKicksGattServer(
            device_name=self.device_name,
            adapter_name=self.adapter_name,
            on_haptic_command=self._handle_haptic_command,
            on_studio_command=self._handle_studio_command,
            on_studio_cancel=self._handle_studio_cancel,
        )

    def set_studio_manager(self, studio_manager: StudioManager) -> None:
        self._studio_manager = studio_manager
        # Attach status listener to forward StudioManager state changes to BLE Studio Control characteristic
        if hasattr(studio_manager, "add_status_listener"):
            studio_manager.add_status_listener(self.gatt_server.notify_studio_status)

    def start(self) -> None:
        LOGGER.info("starting_ble_transport device_name=%s", self.device_name)
        try:
            self.gatt_server.start()
        except BluetoothAdapterError as err:
            LOGGER.error(
                "ble_transport_start_failed: %s. "
                "Le serveur BLE est inactif mais le service Edge continue d'opérer.",
                err,
            )

    def stop(self) -> None:
        LOGGER.info("stopping_ble_transport")
        self.gatt_server.stop()

    def publish_detection(self, event: DetectionEvent) -> None:
        """Format and notify an activity or fall detection alert over BLE."""
        LOGGER.info(
            "ble_publish_detection event_type=%s confidence=%.2f",
            event.event_type,
            event.confidence,
        )
        self.gatt_server.notify_detection(
            event_type=event.event_type,
            confidence=event.confidence,
            timestamp_sec=event.timestamp,
        )

    def publish_batch(self, batch: TelemetryBatch) -> None:
        """Déversement par paquets (Burst Transfer) pour les sessions Studio."""
        is_studio = (
            batch.metadata.flush_trigger == "studio"
            or bool(batch.metadata.session_id)
        )

        if not is_studio:
            # In nominal BLE mode, continuous telemetry is not streamed via burst packets
            LOGGER.debug("ignoring_nominal_telemetry_batch_in_ble_mode count=%d", len(batch.readings))
            return

        LOGGER.info(
            "ble_burst_transfer_start session_id=%s count=%d",
            batch.metadata.session_id,
            len(batch.readings),
        )

        # 1. Notify FINISHED on Studio Control before starting the burst transfer
        sample_count = len(batch.readings)
        sess_id = batch.metadata.session_id or ""
        self.gatt_server.notify_studio_status(f"FINISHED {sample_count} {sess_id}".strip())

        # 2. Split telemetry into BLE MTU-adapted packets
        start_ts = batch.metadata.window_start.timestamp() if hasattr(batch.metadata.window_start, "timestamp") else None
        packets = packetize_readings(
            readings=batch.readings,
            att_mtu=self.att_mtu,
            session_start_timestamp=start_ts,
        )

        # 3. Stream sequentially over Studio Data Burst
        sent_count = self.gatt_server.send_burst_packets(packets)
        LOGGER.info(
            "ble_burst_transfer_complete total_packets=%d samples=%d",
            sent_count,
            sample_count,
        )

    def publish_arduino_response(self, line: str) -> None:
        LOGGER.debug("ble_arduino_response_received line=%s", line)

    # --- Internal Handlers ---

    def _handle_haptic_command(self, cmd: HapticCommand) -> None:
        if self._on_haptic_command:
            self._on_haptic_command(cmd)

    def _handle_studio_command(self, config: StudioCaptureConfig) -> bool:
        if self._on_studio_command:
            return self._on_studio_command(config)
        return False

    def _handle_studio_cancel(self) -> None:
        if self._studio_manager:
            self._studio_manager.cancel()
