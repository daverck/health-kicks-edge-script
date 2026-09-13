from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from healthkicks_edge.schemas import (
        DetectionEvent,
        HapticCommand,
        StudioCaptureConfig,
        TelemetryBatch,
    )
    from healthkicks_edge.studio_manager import StudioManager


class Transport(ABC):
    """Abstract Base Class for HealthKicks Edge communication transports (MQTT or BLE)."""

    @abstractmethod
    def start(self) -> None:
        """Start the transport service, event loops, or network/BLE listeners."""
        pass

    @abstractmethod
    def stop(self) -> None:
        """Stop the transport cleanly, disconnect clients, and release hardware resources."""
        pass

    @abstractmethod
    def publish_batch(self, batch: TelemetryBatch) -> None:
        """Publish a telemetry batch (via MQTT topic or BLE Burst Transfer)."""
        pass

    @abstractmethod
    def publish_detection(self, event: DetectionEvent) -> None:
        """Publish an activity or fall detection alert."""
        pass

    @abstractmethod
    def publish_arduino_response(self, line: str) -> None:
        """Publish/forward an Arduino serial acknowledgment or status response."""
        pass

    @abstractmethod
    def set_studio_manager(self, studio_manager: StudioManager) -> None:
        """Attach the StudioManager instance for session lifecycle coordination."""
        pass

    def heartbeat_loop(self) -> None:
        """Periodic background loop for transports that require keepalive/heartbeats."""
        pass
