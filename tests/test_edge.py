from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

import mqtt_handler
from ai_engine import EdgeAI
from mqtt_handler import MQTTHandler
from schemas import FallEvent, FallPayload, Header, HapticCommand
from serial_handler import SerialHandler


TEST_DEVICE_ID = "HK-1"


@pytest.fixture
def haptic_topic() -> str:
    return f"healthkicks/v1/{TEST_DEVICE_ID}/commands/haptic"


@pytest.fixture
def haptic_clean_payload() -> bytes:
    return b'{"intensity": 80, "duration_ms": 500}'


@pytest.fixture
def fake_mqtt_message(haptic_topic: str, haptic_clean_payload: bytes):
    def _create(topic: str | None = None, payload: bytes | None = None):
        msg = type("MQTTMessage", (), {})()
        msg.topic = topic if topic is not None else haptic_topic
        msg.payload = payload if payload is not None else haptic_clean_payload
        return msg

    return _create


class FakeMQTTClient:
    def __init__(self, **_: object) -> None:
        self.will: tuple[object, ...] | None = None

    def username_pw_set(self, *_: object) -> None:
        pass

    def will_set(self, *args: object, **kwargs: object) -> None:
        self.will = (*args, kwargs)

    def reconnect_delay_set(self, **_: object) -> None:
        pass

    def publish(self, *args: object, **kwargs: object) -> object:
        return type("Result", (), {"rc": 0})()


def test_fall_event_schema() -> None:
    event = FallEvent(
        header=Header(device_id=TEST_DEVICE_ID, timestamp=datetime.now(timezone.utc)),
        payload=FallPayload(
            ax=1, ay=2, az=30, gx=0, gy=0, gz=0, anomaly_score=30
        ),
    )
    assert event.header.schema_version == "1.0"
    assert event.header.msg_id.version == 4


def test_haptic_command_constraints() -> None:
    # Commande valide sans device_id
    cmd = HapticCommand(intensity=255, duration_ms=10000)
    assert cmd.intensity == 255
    assert cmd.duration_ms == 10000

    # Payload épuré sans device_id accepté
    cmd_clean = HapticCommand.model_validate_json('{"intensity": 80, "duration_ms": 500}')
    assert cmd_clean.intensity == 80
    assert cmd_clean.duration_ms == 500

    # Le payload avec device_id doit être rejeté (StrictModel interdit les champs supplémentaires)
    with pytest.raises(ValidationError):
        HapticCommand.model_validate_json(
            f'{{"device_id": "{TEST_DEVICE_ID}", "intensity": 80, "duration_ms": 500}}'
        )

    with pytest.raises(ValidationError):
        HapticCommand(intensity=256, duration_ms=300)
    with pytest.raises(ValidationError):
        HapticCommand(intensity=10, duration_ms=49)


def test_mqtt_handler_on_message_clean_payload(
    monkeypatch: pytest.MonkeyPatch,
    fake_mqtt_message,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(mqtt_handler.mqtt, "Client", FakeMQTTClient)
    received: list[HapticCommand] = []
    handler = MQTTHandler(
        "localhost", 1883, f"{TEST_DEVICE_ID}-edge", None, None, TEST_DEVICE_ID,
        f"healthkicks/v1/{TEST_DEVICE_ID}/telemetry/raw",
        f"healthkicks/v1/{TEST_DEVICE_ID}/events/fall",
        f"healthkicks/v1/{TEST_DEVICE_ID}/commands/haptic",
        f"healthkicks/v1/{TEST_DEVICE_ID}/status",
        f"healthkicks/v1/{TEST_DEVICE_ID}/commands/ack",
        30,
        received.append,
    )
    msg = fake_mqtt_message()
    with caplog.at_level("INFO"):
        handler._on_message(handler.client, None, msg)  # type: ignore[arg-type]

    assert len(received) == 1
    assert received[0].intensity == 80
    assert received[0].duration_ms == 500
    assert "haptic_command_received intensity=80 duration=500" in caplog.text


def test_mqtt_handler_on_message_rejects_payload_with_device_id(
    monkeypatch: pytest.MonkeyPatch,
    fake_mqtt_message,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(mqtt_handler.mqtt, "Client", FakeMQTTClient)
    received: list[HapticCommand] = []
    handler = MQTTHandler(
        "localhost", 1883, f"{TEST_DEVICE_ID}-edge", None, None, TEST_DEVICE_ID,
        f"healthkicks/v1/{TEST_DEVICE_ID}/telemetry/raw",
        f"healthkicks/v1/{TEST_DEVICE_ID}/events/fall",
        f"healthkicks/v1/{TEST_DEVICE_ID}/commands/haptic",
        f"healthkicks/v1/{TEST_DEVICE_ID}/status",
        f"healthkicks/v1/{TEST_DEVICE_ID}/commands/ack",
        30,
        received.append,
    )
    msg = fake_mqtt_message(
        payload=f'{{"device_id": "{TEST_DEVICE_ID}", "intensity": 80, "duration_ms": 500}}'.encode()
    )
    with caplog.at_level("WARNING"):
        handler._on_message(handler.client, None, msg)  # type: ignore[arg-type]

    assert len(received) == 0
    assert "mqtt_command_invalid" in caplog.text


def test_settings_dynamic_topics_default_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    from config import Settings

    # Vérification des valeurs par défaut dynamiques avec fallback HK-1
    monkeypatch.delenv("EDGE_DEVICE_ID", raising=False)
    monkeypatch.delenv("EDGE_TELEMETRY_TOPIC", raising=False)
    monkeypatch.delenv("EDGE_FALL_TOPIC", raising=False)
    monkeypatch.delenv("EDGE_COMMAND_TOPIC", raising=False)
    monkeypatch.delenv("EDGE_STATUS_TOPIC", raising=False)
    monkeypatch.delenv("EDGE_ACK_TOPIC", raising=False)
    monkeypatch.delenv("EDGE_MQTT_CLIENT_ID", raising=False)

    settings = Settings.from_env()
    assert settings.device_id == TEST_DEVICE_ID
    prefix = f"healthkicks/v1/{TEST_DEVICE_ID}"
    assert settings.telemetry_topic == f"{prefix}/telemetry/raw"
    assert settings.fall_topic == f"{prefix}/events/fall"
    assert settings.command_topic == f"{prefix}/commands/haptic"
    assert settings.status_topic == f"{prefix}/status"
    assert settings.ack_topic == f"{prefix}/commands/ack"
    assert settings.mqtt_client_id == f"{TEST_DEVICE_ID}-edge"

    # Vérification avec surcharge via variable d'environnement
    custom_id = "HK-2"
    monkeypatch.setenv("EDGE_DEVICE_ID", custom_id)
    settings_custom = Settings.from_env()
    assert settings_custom.device_id == custom_id
    custom_prefix = f"healthkicks/v1/{custom_id}"
    assert settings_custom.telemetry_topic == f"{custom_prefix}/telemetry/raw"
    assert settings_custom.fall_topic == f"{custom_prefix}/events/fall"
    assert settings_custom.command_topic == f"{custom_prefix}/commands/haptic"
    assert settings_custom.status_topic == f"{custom_prefix}/status"
    assert settings_custom.ack_topic == f"{custom_prefix}/commands/ack"
    assert settings_custom.mqtt_client_id == f"{custom_id}-edge"


def test_lwt_is_flat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mqtt_handler.mqtt, "Client", FakeMQTTClient)
    handler = MQTTHandler(
        "localhost", 1883, "client", None, None, TEST_DEVICE_ID,
        "telemetry", "fall", "command", "status", "ack", 30, lambda _: None
    )
    assert handler.client.will is not None
    assert json.loads(handler.client.will[1]) == {
        "state": "offline", "reason": "unexpected_disconnection"
    }


def test_missing_model_uses_heuristic(tmp_path) -> None:
    falls: list[FallEvent] = []
    ai = EdgeAI(
        TEST_DEVICE_ID, str(tmp_path / "model.joblib"), 32, 0,
        lambda _: None, falls.append, lambda: None
    )
    ai.process({"ax": 0, "ay": 0, "az": 30, "gx": 0, "gy": 0, "gz": 0})
    assert len(falls) == 1


def test_expired_command_is_dropped() -> None:
    handler = SerialHandler("/dev/null", 115200, threading.Event(), 2, lambda _: None)
    class FakeSerial:
        def __init__(self) -> None:
            self.writes: list[bytes] = []
        def write(self, payload: bytes) -> None:
            self.writes.append(payload)
        def flush(self) -> None:
            pass
    fake_serial = FakeSerial()
    handler._serial = fake_serial  # type: ignore[assignment]
    handler.enqueue_haptic(255, 500)
    time.sleep(2.05)
    handler._write_pending()
    assert fake_serial.writes == []


def test_data_prefixed_serial_telemetry_is_accepted() -> None:
    received: list[dict[str, float]] = []
    handler = SerialHandler(
        "/dev/null", 115200, threading.Event(), 2, received.append
    )
    handler._handle_line(
        b'DATA:{"ax":0.96,"ay":0.09,"az":0.25,"gx":0.2,"gy":-0.1,"gz":-0.1}\n'
    )
    assert received == [
        {"ax": 0.96, "ay": 0.09, "az": 0.25, "gx": 0.2, "gy": -0.1, "gz": -0.1}
    ]


def _telemetry(**payload: float):
    from datetime import datetime, timezone as tz
    from schemas import Header, ImuPayload, Telemetry

    base = {"ax": 0.0, "ay": 0.0, "az": 30.0, "gx": 0.0, "gy": 0.0, "gz": 0.0}
    base.update(payload)
    return Telemetry(
        header=Header(device_id=TEST_DEVICE_ID, timestamp=datetime.now(tz.utc)),
        payload=ImuPayload(**base),
    )


def test_buffer_flushes_on_max_size() -> None:
    from telemetry_buffer import TelemetryBuffer

    batches = []
    buffer = TelemetryBuffer(TEST_DEVICE_ID, max_size=3, flush_interval=60.0, publish=batches.append)
    for _ in range(2):
        assert buffer.append(_telemetry()) is None
    assert buffer.append(_telemetry()) == "max_size"
    assert buffer.flush("max_size") == 3
    assert len(batches) == 1
    batch = batches[0]
    assert batch.metadata.sample_count == 3
    assert batch.metadata.flush_trigger == "max_size"
    assert len(batch.readings) == 3
    assert batch.metadata.window_start <= batch.metadata.window_end


def test_buffer_flushes_on_time_interval() -> None:
    from telemetry_buffer import TelemetryBuffer

    batches = []
    buffer = TelemetryBuffer(TEST_DEVICE_ID, max_size=100, flush_interval=0.05, publish=batches.append)
    buffer.append(_telemetry())
    assert buffer.due() is False
    time.sleep(0.08)
    assert buffer.due() is True
    assert buffer.flush("time_interval") == 1
    assert batches[0].metadata.flush_trigger == "time_interval"
    assert buffer.due() is False
