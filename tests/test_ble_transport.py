import struct
from datetime import datetime, timezone
import pytest

from healthkicks_edge.config import Settings
from healthkicks_edge.schemas import (
    BatchMetadata,
    DetectionEvent,
    DetectionMetadata,
    HapticCommand,
    Header,
    ImuPayload,
    StudioCaptureConfig,
    Telemetry,
    TelemetryBatch,
)
from healthkicks_edge.transport.base import Transport
from healthkicks_edge.transport.ble.constants import (
    STATE_CODE_FALL_FORWARD,
    STATE_CODE_WALK,
)
from healthkicks_edge.transport.ble.gatt_server import HealthKicksGattServer
from healthkicks_edge.transport.ble_transport import BleTransport
from healthkicks_edge.transport.factory import create_transport
from healthkicks_edge.transport.mqtt_transport import MqttTransport


def test_gatt_server_write_haptic_callback():
    received_cmds: list[HapticCommand] = []
    server = HealthKicksGattServer(on_haptic_command=lambda cmd: received_cmds.append(cmd))

    # Test valid 4-byte payload [pattern_id, intensity, duration_ms_hi, duration_ms_lo]
    # intensity=200, duration=500 -> [0x00, 200, 0x01, 0xF4]
    payload_4b = struct.pack(">BBH", 0, 200, 500)
    server._handle_write_haptic(list(payload_4b))

    assert len(received_cmds) == 1
    assert received_cmds[0].intensity == 200
    assert received_cmds[0].duration_ms == 500


def test_gatt_server_write_studio_control_start():
    studio_cmds: list[StudioCaptureConfig] = []
    server = HealthKicksGattServer(on_studio_command=lambda cfg: studio_cmds.append(cfg) or True)

    command_str = "START walk 5.0 session-uuid-1234"
    server._handle_write_studio_control(list(command_str.encode("utf-8")))

    assert len(studio_cmds) == 1
    assert studio_cmds[0].label == "walk"
    assert studio_cmds[0].duration_sec == 5.0
    assert studio_cmds[0].session_id == "session-uuid-1234"


def test_gatt_server_write_studio_control_cancel():
    cancelled = False

    def on_cancel():
        nonlocal cancelled
        cancelled = True

    server = HealthKicksGattServer(on_studio_cancel=on_cancel)
    server._handle_write_studio_control(list(b"CANCEL"))
    assert cancelled is True


def test_gatt_server_notify_detection_formatting():
    server = HealthKicksGattServer()
    server.notify_detection(event_type="fall_forward", confidence=0.89, timestamp_sec=1726224000)

    raw_bytes = bytes(server._handle_read_activity())
    assert len(raw_bytes) == 7

    state_code, confidence, ts, flags = struct.unpack(">BBI B", raw_bytes)
    assert state_code == STATE_CODE_FALL_FORWARD
    assert confidence == 89
    assert ts == 1726224000
    assert flags == 0x01  # fall flag set


def test_ble_transport_publish_batch_burst():
    transport = BleTransport(device_id="HK-TEST", device_name="HealthKicks-Test")

    now = datetime.now(timezone.utc)
    readings = [
        Telemetry(
            header=Header(device_id="HK-TEST", timestamp=now),
            payload=ImuPayload(ax=0.1, ay=0.9, az=0.2, gx=1.0, gy=2.0, gz=3.0),
        )
        for _ in range(25)
    ]
    batch = TelemetryBatch(
        header=Header(device_id="HK-TEST", timestamp=now),
        metadata=BatchMetadata(
            sample_count=25,
            window_start=now,
            window_end=now,
            flush_trigger="studio",
            session_id="studio-test-id",
            label="test_run",
        ),
        readings=readings,
    )

    # Should execute burst transfer cleanly without error
    transport.publish_batch(batch)


def test_create_transport_factory(monkeypatch):
    # 1. Default / MQTT transport
    settings_mqtt = Settings.from_env()
    t_mqtt = create_transport(
        settings_mqtt,
        on_haptic_command=lambda _: None,
        on_studio_command=lambda _: True,
    )
    assert isinstance(t_mqtt, MqttTransport)

    # 2. BLE transport
    monkeypatch.setenv("EDGE_TRANSPORT", "ble")
    settings_ble = Settings.from_env()
    t_ble = create_transport(
        settings_ble,
        on_haptic_command=lambda _: None,
        on_studio_command=lambda _: True,
    )
    assert isinstance(t_ble, BleTransport)
    assert t_ble.device_id == settings_ble.device_id
