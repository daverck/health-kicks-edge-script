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
    command_topic: str
    status_topic: str
    ack_topic: str
    heartbeat_interval_seconds: int
    command_ttl_seconds: float
    buffer_max_size: int
    buffer_flush_interval_seconds: float
    model_path: str
    log_level: str
    studio_command_topic: str = ""
    continuously_send_telemetry: bool = False
    detection_topic: str = ""
    inference_interval_seconds: float = 0.25
    confidence_threshold: float = 0.65
    detection_cooldown_seconds: float = 5.0
    min_fall_impact_threshold: float = 18.0

    @classmethod
    def from_env(cls) -> Settings:
        device_id = os.getenv("EDGE_DEVICE_ID", "HK-1")
        prefix = f"healthkicks/v1/{device_id}"

        default_model_path = "/opt/healthkicks_edge/models/activity_classifier.joblib"
        repo_model_path = os.path.join(os.path.dirname(__file__), "models", "activity_classifier.joblib")
        ota_model_path = "/var/lib/healthkicks/models/activity_classifier.joblib"
        dev_model_path = r"F:\Programmation\health-kicks\scripts\models\activity_classifier.joblib"
        legacy_model_path = "/var/lib/healthkicks/model.joblib"

        env_model_path = os.getenv("EDGE_MODEL_PATH")
        if env_model_path:
            resolved_model_path = env_model_path
        elif os.path.exists(ota_model_path):
            resolved_model_path = ota_model_path
        elif os.path.exists(default_model_path):
            resolved_model_path = default_model_path
        elif os.path.exists(repo_model_path):
            resolved_model_path = repo_model_path
        elif os.path.exists(dev_model_path):
            resolved_model_path = dev_model_path
        elif os.path.exists(legacy_model_path):
            resolved_model_path = legacy_model_path
        else:
            resolved_model_path = default_model_path

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
            command_topic=os.getenv("EDGE_COMMAND_TOPIC", f"{prefix}/commands/haptic"),
            status_topic=os.getenv("EDGE_STATUS_TOPIC", f"{prefix}/status"),
            ack_topic=os.getenv("EDGE_ACK_TOPIC", f"{prefix}/commands/ack"),
            heartbeat_interval_seconds=int(os.getenv("EDGE_HEARTBEAT_INTERVAL", "30")),
            command_ttl_seconds=float(os.getenv("EDGE_COMMAND_TTL", "2")),
            buffer_max_size=int(os.getenv("EDGE_BUFFER_MAX_SIZE", "50")),
            buffer_flush_interval_seconds=float(os.getenv("EDGE_BUFFER_FLUSH_INTERVAL_SEC", "2.0")),
            model_path=resolved_model_path,
            log_level=os.getenv("EDGE_LOG_LEVEL", "INFO").upper(),
            studio_command_topic=os.getenv(
                "EDGE_STUDIO_COMMAND_TOPIC", f"{prefix}/commands/studio/start"
            ),
            continuously_send_telemetry=(
                os.getenv("EDGE_CONTINUOUSLY_SEND_TELEMETRY", "false").lower()
                in ("true", "1", "yes", "on")
            ),
            detection_topic=os.getenv(
                "EDGE_DETECTION_TOPIC", f"{prefix}/events/detection"
            ),
            inference_interval_seconds=float(
                os.getenv("EDGE_INFERENCE_INTERVAL_SEC", "0.25")
            ),
            confidence_threshold=float(
                os.getenv("EDGE_CONFIDENCE_THRESHOLD", "0.65")
            ),
            detection_cooldown_seconds=float(
                os.getenv("EDGE_DETECTION_COOLDOWN_SEC", "5.0")
            ),
            min_fall_impact_threshold=float(
                os.getenv("EDGE_MIN_FALL_IMPACT_THRESHOLD", "18.0")
            ),
        )
