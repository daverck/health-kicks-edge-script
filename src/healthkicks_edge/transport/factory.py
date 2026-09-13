"""Factory to instantiate the appropriate Transport based on configuration."""
from __future__ import annotations

import logging
from typing import Callable

from healthkicks_edge.config import Settings
from healthkicks_edge.mqtt_handler import MQTTHandler
from healthkicks_edge.schemas import HapticCommand, StudioCaptureConfig
from healthkicks_edge.transport.base import Transport
from healthkicks_edge.transport.ble_transport import BleTransport
from healthkicks_edge.transport.mqtt_transport import MqttTransport

LOGGER = logging.getLogger(__name__)


def create_transport(
    settings: Settings,
    on_haptic_command: Callable[[HapticCommand], None],
    on_studio_command: Callable[[StudioCaptureConfig], bool],
) -> Transport:
    """Instantiate either BleTransport or MqttTransport depending on settings.transport."""
    transport_type = settings.transport.lower().strip()

    if transport_type == "ble":
        LOGGER.info("creating_ble_transport device_id=%s", settings.device_id)
        return BleTransport(
            device_id=settings.device_id,
            device_name=settings.ble_device_name,
            adapter_name=settings.ble_adapter,
            on_haptic_command=on_haptic_command,
            on_studio_command=on_studio_command,
        )

    LOGGER.info("creating_mqtt_transport host=%s port=%d", settings.mqtt_host, settings.mqtt_port)
    mqtt_handler = MQTTHandler(
        host=settings.mqtt_host,
        port=settings.mqtt_port,
        client_id=settings.mqtt_client_id,
        username=settings.mqtt_username,
        password=settings.mqtt_password,
        device_id=settings.device_id,
        telemetry_topic=settings.telemetry_topic,
        command_topic=settings.command_topic,
        status_topic=settings.status_topic,
        ack_topic=settings.ack_topic,
        heartbeat_interval=settings.heartbeat_interval_seconds,
        on_haptic_command=on_haptic_command,
        studio_command_topic=settings.studio_command_topic,
        on_studio_command=on_studio_command,
        detection_topic=settings.detection_topic,
    )
    return MqttTransport(mqtt_handler)
