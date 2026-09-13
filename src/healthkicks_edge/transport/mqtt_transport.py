"""MQTT Transport implementation adapting MQTTHandler to the Transport ABC."""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from healthkicks_edge.mqtt_handler import MQTTHandler
from healthkicks_edge.schemas import (
    DetectionEvent,
    HapticCommand,
    StudioCaptureConfig,
    TelemetryBatch,
)
from healthkicks_edge.transport.base import Transport

if TYPE_CHECKING:
    from healthkicks_edge.studio_manager import StudioManager


class MqttTransport(Transport):
    """Wraps MQTTHandler to fulfill the common Transport interface."""

    def __init__(
        self,
        handler: MQTTHandler,
    ) -> None:
        self.handler = handler

    def start(self) -> None:
        self.handler.start()

    def stop(self) -> None:
        self.handler.stop()

    def publish_batch(self, batch: TelemetryBatch) -> None:
        self.handler.publish_batch(batch)

    def publish_detection(self, event: DetectionEvent) -> None:
        self.handler.publish_detection(event)

    def publish_arduino_response(self, line: str) -> None:
        self.handler.publish_arduino_response(line)

    def set_studio_manager(self, studio_manager: StudioManager) -> None:
        self.handler.set_studio_manager(studio_manager)

    def heartbeat_loop(self) -> None:
        self.handler.heartbeat_loop()
