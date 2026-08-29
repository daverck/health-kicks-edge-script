from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    device_id: str
    serial_device: str
    serial_baudrate: int
    mqtt_host: str
    mqtt_port: int
    mqtt_client_id: str
    mqtt_username: str | None
    mqtt_password: str | None
    telemetry_topic: str
    fall_topic: str
    command_topic: str
    status_topic: str
    ack_topic: str
    heartbeat_interval_seconds: int
    command_ttl_seconds: float
    fall_cooldown_seconds: float
    buffer_max_size: int
    buffer_flush_interval_seconds: float
    model_path: str
    model_window_size: int
    log_level: str

    @classmethod
    def from_env(cls) -> Settings:
        device_id = os.getenv("EDGE_DEVICE_ID", "healthkicks-pi-001")
        prefix = f"healthkicks/v1/{device_id}"
        return cls(
            device_id=device_id,
            serial_device=os.getenv("EDGE_SERIAL_DEVICE", "/dev/ttyUSB0"),
            serial_baudrate=int(os.getenv("EDGE_SERIAL_BAUDRATE", "115200")),
            mqtt_host=os.getenv("EDGE_MQTT_HOST", "localhost"),
            mqtt_port=int(os.getenv("EDGE_MQTT_PORT", "1883")),
            mqtt_client_id=os.getenv("EDGE_MQTT_CLIENT_ID", f"{device_id}-edge"),
            mqtt_username=os.getenv("EDGE_MQTT_USERNAME") or None,
            mqtt_password=os.getenv("EDGE_MQTT_PASSWORD") or None,
            telemetry_topic=os.getenv("EDGE_TELEMETRY_TOPIC", f"{prefix}/telemetry/raw"),
            fall_topic=os.getenv("EDGE_FALL_TOPIC", f"{prefix}/events/fall"),
            command_topic=os.getenv("EDGE_COMMAND_TOPIC", f"{prefix}/commands/haptic"),
            status_topic=os.getenv("EDGE_STATUS_TOPIC", f"{prefix}/status"),
            ack_topic=os.getenv("EDGE_ACK_TOPIC", f"{prefix}/commands/ack"),
            heartbeat_interval_seconds=int(os.getenv("EDGE_HEARTBEAT_INTERVAL", "30")),
            command_ttl_seconds=float(os.getenv("EDGE_COMMAND_TTL", "2")),
            fall_cooldown_seconds=float(os.getenv("EDGE_FALL_COOLDOWN", "3")),
            buffer_max_size=int(os.getenv("EDGE_BUFFER_MAX_SIZE", "50")),
            buffer_flush_interval_seconds=float(os.getenv("EDGE_BUFFER_FLUSH_INTERVAL_SEC", "2.0")),
            model_path=os.getenv("EDGE_MODEL_PATH", "/var/lib/healthkicks/model.joblib"),
            model_window_size=int(os.getenv("EDGE_MODEL_WINDOW_SIZE", "32")),
            log_level=os.getenv("EDGE_LOG_LEVEL", "INFO").upper(),
        )
