from __future__ import annotations

import json
from unittest.mock import MagicMock
import pytest

import mqtt_handler
from mqtt_handler import MQTTHandler
from schemas import StudioCaptureConfig

TEST_DEVICE_ID = "HK-1"


class FakeMQTTClient:
    def __init__(self, *args: object, **kwargs: object) -> None:
        self.published_messages: list[tuple[str, str, int]] = []
        self.subscriptions: list[tuple[str, int]] = []
        self.will = None

    def username_pw_set(self, *args: object, **kwargs: object) -> None:
        pass

    def will_set(self, *args: object, **kwargs: object) -> None:
        self.will = (*args, kwargs)

    def reconnect_delay_set(self, **_: object) -> None:
        pass

    def subscribe(self, topic: str, qos: int = 0) -> None:
        self.subscriptions.append((topic, qos))

    def publish(self, topic: str, payload: str, qos: int = 0, retain: bool = False) -> object:
        self.published_messages.append((topic, payload, qos))
        return type("Result", (), {"rc": 0})()


def _make_msg(topic: str, payload: bytes):
    msg = type("MQTTMessage", (), {})()
    msg.topic = topic
    msg.payload = payload
    return msg


@pytest.fixture(autouse=True)
def mock_paho_client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(mqtt_handler.mqtt, "Client", FakeMQTTClient)


def test_mqtt_subscribes_to_studio_topic_on_connect() -> None:
    handler = MQTTHandler(
        "localhost", 1883, f"{TEST_DEVICE_ID}-edge", None, None, TEST_DEVICE_ID,
        f"healthkicks/v1/{TEST_DEVICE_ID}/telemetry/raw",
        f"healthkicks/v1/{TEST_DEVICE_ID}/events/fall",
        f"healthkicks/v1/{TEST_DEVICE_ID}/commands/haptic",
        f"healthkicks/v1/{TEST_DEVICE_ID}/status",
        f"healthkicks/v1/{TEST_DEVICE_ID}/commands/ack",
        30,
        lambda _: None,
    )

    fake_client: FakeMQTTClient = handler.client  # type: ignore[assignment]
    handler._on_connect(fake_client, None, {}, 0, None)  # type: ignore[arg-type]

    assert (f"healthkicks/v1/{TEST_DEVICE_ID}/commands/haptic", 1) in fake_client.subscriptions
    assert (f"healthkicks/v1/{TEST_DEVICE_ID}/commands/studio/start", 1) in fake_client.subscriptions


def test_mqtt_studio_start_valid_command() -> None:
    mock_studio = MagicMock()
    mock_studio.start_capture.return_value = True

    handler = MQTTHandler(
        "localhost", 1883, f"{TEST_DEVICE_ID}-edge", None, None, TEST_DEVICE_ID,
        f"healthkicks/v1/{TEST_DEVICE_ID}/telemetry/raw",
        f"healthkicks/v1/{TEST_DEVICE_ID}/events/fall",
        f"healthkicks/v1/{TEST_DEVICE_ID}/commands/haptic",
        f"healthkicks/v1/{TEST_DEVICE_ID}/status",
        f"healthkicks/v1/{TEST_DEVICE_ID}/commands/ack",
        30,
        lambda _: None,
        studio_manager=mock_studio,
    )

    topic = f"healthkicks/v1/{TEST_DEVICE_ID}/commands/studio/start"
    payload = json.dumps({
        "session_id": "session-cloud-123",
        "label": "run",
        "duration_sec": 7.5,
        "pulse_count": 3,
        "pulse_intensity": 200,
    }).encode("utf-8")

    msg = _make_msg(topic, payload)
    handler._on_message(handler.client, None, msg)  # type: ignore[arg-type]

    # Vérifie que start_capture a été appelé avec un StudioCaptureConfig valide
    mock_studio.start_capture.assert_called_once()
    called_config: StudioCaptureConfig = mock_studio.start_capture.call_args[0][0]
    assert called_config.session_id == "session-cloud-123"
    assert called_config.label == "run"
    assert called_config.duration_sec == 7.5
    assert called_config.pulse_count == 3
    assert called_config.pulse_intensity == 200

    # Vérifie la publication de l'acquittement 'started' sur ack_topic
    fake_client: FakeMQTTClient = handler.client  # type: ignore[assignment]
    ack_messages = [
        json.loads(p) for t, p, _ in fake_client.published_messages
        if t == f"healthkicks/v1/{TEST_DEVICE_ID}/commands/ack"
    ]
    assert len(ack_messages) == 1
    assert ack_messages[0] == {
        "command": "studio/start",
        "session_id": "session-cloud-123",
        "status": "started",
    }


def test_mqtt_studio_start_busy_session(caplog: pytest.LogCaptureFixture) -> None:
    mock_studio = MagicMock()
    mock_studio.start_capture.return_value = False  # Session occupée

    handler = MQTTHandler(
        "localhost", 1883, f"{TEST_DEVICE_ID}-edge", None, None, TEST_DEVICE_ID,
        f"healthkicks/v1/{TEST_DEVICE_ID}/telemetry/raw",
        f"healthkicks/v1/{TEST_DEVICE_ID}/events/fall",
        f"healthkicks/v1/{TEST_DEVICE_ID}/commands/haptic",
        f"healthkicks/v1/{TEST_DEVICE_ID}/status",
        f"healthkicks/v1/{TEST_DEVICE_ID}/commands/ack",
        30,
        lambda _: None,
        studio_manager=mock_studio,
    )

    topic = f"healthkicks/v1/{TEST_DEVICE_ID}/commands/studio/start"
    payload = json.dumps({"session_id": "sess-busy-456", "label": "walk"}).encode("utf-8")

    with caplog.at_level("WARNING"):
        msg = _make_msg(topic, payload)
        handler._on_message(handler.client, None, msg)  # type: ignore[arg-type]

    assert "studio_session_busy" in caplog.text

    # Vérifie l'envoi de l'acquittement 'busy'
    fake_client: FakeMQTTClient = handler.client  # type: ignore[assignment]
    ack_messages = [
        json.loads(p) for t, p, _ in fake_client.published_messages
        if t == f"healthkicks/v1/{TEST_DEVICE_ID}/commands/ack"
    ]
    assert len(ack_messages) == 1
    assert ack_messages[0]["status"] == "busy"
    assert ack_messages[0]["session_id"] == "sess-busy-456"


def test_mqtt_studio_start_malformed_json(caplog: pytest.LogCaptureFixture) -> None:
    mock_studio = MagicMock()
    handler = MQTTHandler(
        "localhost", 1883, f"{TEST_DEVICE_ID}-edge", None, None, TEST_DEVICE_ID,
        f"healthkicks/v1/{TEST_DEVICE_ID}/telemetry/raw",
        f"healthkicks/v1/{TEST_DEVICE_ID}/events/fall",
        f"healthkicks/v1/{TEST_DEVICE_ID}/commands/haptic",
        f"healthkicks/v1/{TEST_DEVICE_ID}/status",
        f"healthkicks/v1/{TEST_DEVICE_ID}/commands/ack",
        30,
        lambda _: None,
        studio_manager=mock_studio,
    )

    topic = f"healthkicks/v1/{TEST_DEVICE_ID}/commands/studio/start"
    msg = _make_msg(topic, b"not-a-valid-json{")

    with caplog.at_level("WARNING"):
        handler._on_message(handler.client, None, msg)  # type: ignore[arg-type]

    assert "mqtt_studio_command_invalid" in caplog.text
    mock_studio.start_capture.assert_not_called()


def test_mqtt_studio_start_strict_model_extra_fields(caplog: pytest.LogCaptureFixture) -> None:
    mock_studio = MagicMock()
    handler = MQTTHandler(
        "localhost", 1883, f"{TEST_DEVICE_ID}-edge", None, None, TEST_DEVICE_ID,
        f"healthkicks/v1/{TEST_DEVICE_ID}/telemetry/raw",
        f"healthkicks/v1/{TEST_DEVICE_ID}/events/fall",
        f"healthkicks/v1/{TEST_DEVICE_ID}/commands/haptic",
        f"healthkicks/v1/{TEST_DEVICE_ID}/status",
        f"healthkicks/v1/{TEST_DEVICE_ID}/commands/ack",
        30,
        lambda _: None,
        studio_manager=mock_studio,
    )

    topic = f"healthkicks/v1/{TEST_DEVICE_ID}/commands/studio/start"
    payload = json.dumps({
        "session_id": "sess-extra",
        "label": "walk",
        "unexpected_field": "forbidden",
    }).encode("utf-8")

    with caplog.at_level("WARNING"):
        msg = _make_msg(topic, payload)
        handler._on_message(handler.client, None, msg)  # type: ignore[arg-type]

    assert "mqtt_studio_command_invalid" in caplog.text
    mock_studio.start_capture.assert_not_called()


def test_mqtt_studio_start_invalid_constraints(caplog: pytest.LogCaptureFixture) -> None:
    mock_studio = MagicMock()
    handler = MQTTHandler(
        "localhost", 1883, f"{TEST_DEVICE_ID}-edge", None, None, TEST_DEVICE_ID,
        f"healthkicks/v1/{TEST_DEVICE_ID}/telemetry/raw",
        f"healthkicks/v1/{TEST_DEVICE_ID}/events/fall",
        f"healthkicks/v1/{TEST_DEVICE_ID}/commands/haptic",
        f"healthkicks/v1/{TEST_DEVICE_ID}/status",
        f"healthkicks/v1/{TEST_DEVICE_ID}/commands/ack",
        30,
        lambda _: None,
        studio_manager=mock_studio,
    )

    # duration_sec trop grand (> 30.0)
    topic = f"healthkicks/v1/{TEST_DEVICE_ID}/commands/studio/start"
    payload = json.dumps({
        "session_id": "sess-oversize",
        "label": "walk",
        "duration_sec": 45.0,
    }).encode("utf-8")

    with caplog.at_level("WARNING"):
        msg = _make_msg(topic, payload)
        handler._on_message(handler.client, None, msg)  # type: ignore[arg-type]

    assert "mqtt_studio_command_invalid" in caplog.text
    mock_studio.start_capture.assert_not_called()
