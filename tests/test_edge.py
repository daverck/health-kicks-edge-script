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
        header=Header(device_id="pi-1", timestamp=datetime.now(timezone.utc)),
        payload=FallPayload(
            ax=1, ay=2, az=30, gx=0, gy=0, gz=0, anomaly_score=30
        ),
    )
    assert event.header.schema_version == "1.0"
    assert event.header.msg_id.version == 4


def test_haptic_command_constraints() -> None:
    assert HapticCommand(intensity=255, duration_ms=10000)
    with pytest.raises(ValidationError):
        HapticCommand(intensity=256, duration_ms=300)
    with pytest.raises(ValidationError):
        HapticCommand(intensity=10, duration_ms=49)


def test_lwt_is_flat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mqtt_handler.mqtt, "Client", FakeMQTTClient)
    handler = MQTTHandler(
        "localhost", 1883, "client", None, None, "pi-1",
        "telemetry", "fall", "command", "status", "ack", 30, lambda _: None
    )
    assert handler.client.will is not None
    assert json.loads(handler.client.will[1]) == {
        "state": "offline", "reason": "unexpected_disconnection"
    }


def test_missing_model_uses_heuristic(tmp_path) -> None:
    falls: list[FallEvent] = []
    ai = EdgeAI(
        "pi-1", str(tmp_path / "model.joblib"), 32, 0,
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
        header=Header(device_id="pi-1", timestamp=datetime.now(tz.utc)),
        payload=ImuPayload(**base),
    )


def test_buffer_flushes_on_max_size() -> None:
    from telemetry_buffer import TelemetryBuffer

    batches = []
    buffer = TelemetryBuffer("pi-1", max_size=3, flush_interval=60.0, publish=batches.append)
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
    buffer = TelemetryBuffer("pi-1", max_size=100, flush_interval=0.05, publish=batches.append)
    buffer.append(_telemetry())
    assert buffer.due() is False
    time.sleep(0.08)
    assert buffer.due() is True
    assert buffer.flush("time_interval") == 1
    assert batches[0].metadata.flush_trigger == "time_interval"
    assert buffer.due() is False
