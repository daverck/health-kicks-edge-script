from __future__ import annotations

import json
from datetime import datetime, timezone
import pytest
from pydantic import ValidationError

from healthkicks_edge.schemas import BatchMetadata, Header, ImuPayload, Telemetry, TelemetryBatch
from healthkicks_edge.telemetry_buffer import TelemetryBuffer


def _create_sample_telemetry(device_id: str = "HK-1") -> Telemetry:
    return Telemetry(
        header=Header(device_id=device_id, timestamp=datetime.now(timezone.utc)),
        payload=ImuPayload(ax=0.0, ay=1.0, az=9.8, gx=0.0, gy=0.0, gz=0.0),
    )


def test_nominal_telemetry_batch_serialization() -> None:
    now = datetime.now(timezone.utc)
    reading = _create_sample_telemetry("HK-1")
    batch = TelemetryBatch(
        header=Header(device_id="HK-1", timestamp=now),
        metadata=BatchMetadata(
            sample_count=1,
            window_start=now,
            window_end=now,
            flush_trigger="time_interval",
        ),
        readings=[reading],
    )

    assert batch.metadata.session_id is None
    assert batch.metadata.label is None
    assert batch.metadata.flush_trigger == "time_interval"

    # Verify JSON serialization
    raw_json = batch.model_dump_json()
    payload = json.loads(raw_json)

    assert payload["metadata"]["session_id"] is None
    assert payload["metadata"]["label"] is None
    assert payload["metadata"]["flush_trigger"] == "time_interval"
    assert payload["metadata"]["sample_count"] == 1

    # Verify deserialization
    restored = TelemetryBatch.model_validate_json(raw_json)
    assert restored.metadata.session_id is None
    assert restored.metadata.label is None
    assert restored.metadata.flush_trigger == "time_interval"


def test_studio_telemetry_batch_serialization() -> None:
    now = datetime.now(timezone.utc)
    reading = _create_sample_telemetry("HK-1")
    session_id = "test-session-uuid-12345"
    label = "walk"

    batch = TelemetryBatch(
        header=Header(device_id="HK-1", timestamp=now),
        metadata=BatchMetadata(
            sample_count=1,
            window_start=now,
            window_end=now,
            flush_trigger="studio",
            session_id=session_id,
            label=label,
        ),
        readings=[reading],
    )

    assert batch.metadata.session_id == session_id
    assert batch.metadata.label == label
    assert batch.metadata.flush_trigger == "studio"

    # Verify produced JSON
    raw_json = batch.model_dump_json()
    payload = json.loads(raw_json)

    assert payload["metadata"]["session_id"] == session_id
    assert payload["metadata"]["label"] == label
    assert payload["metadata"]["flush_trigger"] == "studio"

    # Pydantic round-trip validation
    restored = TelemetryBatch.model_validate_json(raw_json)
    assert restored.metadata.session_id == session_id
    assert restored.metadata.label == label
    assert restored.metadata.flush_trigger == "studio"


def test_telemetry_buffer_flush_nominal_with_continuous_send() -> None:
    batches: list[TelemetryBatch] = []
    buffer = TelemetryBuffer(
        "HK-1", max_size=10, flush_interval=5.0, publish=batches.append, continuously_send_telemetry=True
    )
    buffer.append(_create_sample_telemetry("HK-1"))

    count = buffer.flush("max_size")
    assert count == 1
    assert len(batches) == 1
    assert batches[0].metadata.flush_trigger == "max_size"
    assert batches[0].metadata.session_id is None
    assert batches[0].metadata.label is None


def test_telemetry_buffer_retains_locally_when_continuously_send_disabled(
    caplog: pytest.LogCaptureFixture,
) -> None:
    batches: list[TelemetryBatch] = []
    buffer = TelemetryBuffer(
        "HK-1", max_size=5, flush_interval=5.0, publish=batches.append, continuously_send_telemetry=False
    )

    # Readings continue to be recorded into local memory via add_reading / append
    reading1 = _create_sample_telemetry("HK-1")
    reading2 = _create_sample_telemetry("HK-1")
    buffer.add_reading(reading1)
    buffer.append(reading2)

    assert len(buffer.recent_readings) == 2

    # Nominal periodic flush (time_interval): Cloud publication skipped
    with caplog.at_level("DEBUG"):
        count = buffer.flush("time_interval")

    assert count == 0
    assert len(batches) == 0
    assert (
        "Nominal telemetry retained locally; Cloud publication skipped (continuously_send_telemetry=False)"
        in caplog.text
    )

    # Staging buffer is emptied to prevent memory leak, but recent_readings remains available
    assert len(buffer.recent_readings) == 2

    # max_size flush also skipped for Cloud publication
    buffer.append(_create_sample_telemetry("HK-1"))
    count_max = buffer.flush("max_size")
    assert count_max == 0
    assert len(batches) == 0


def test_telemetry_buffer_publishes_studio_when_continuously_send_disabled() -> None:
    batches: list[TelemetryBatch] = []
    buffer = TelemetryBuffer(
        "HK-1", max_size=10, flush_interval=5.0, publish=batches.append, continuously_send_telemetry=False
    )
    buffer.append(_create_sample_telemetry("HK-1"))
    buffer.append(_create_sample_telemetry("HK-1"))

    # Studio sessions with session_id bypass the continuous send filter
    session_id = "session-filtered-studio-42"
    label = "jump"
    count = buffer.flush("studio", session_id=session_id, label=label)

    assert count == 2
    assert len(batches) == 1
    assert batches[0].metadata.flush_trigger == "studio"
    assert batches[0].metadata.session_id == session_id
    assert batches[0].metadata.label == label


def test_telemetry_buffer_flush_studio() -> None:
    batches: list[TelemetryBatch] = []
    buffer = TelemetryBuffer("HK-1", max_size=10, flush_interval=5.0, publish=batches.append)
    buffer.append(_create_sample_telemetry("HK-1"))
    buffer.append(_create_sample_telemetry("HK-1"))

    session_id = "session-studio-42"
    label = "run"
    count = buffer.flush("studio", session_id=session_id, label=label)

    assert count == 2
    assert len(batches) == 1
    batch = batches[0]
    assert batch.metadata.flush_trigger == "studio"
    assert batch.metadata.session_id == session_id
    assert batch.metadata.label == label
    assert batch.metadata.sample_count == 2


def test_batch_metadata_field_constraints() -> None:
    now = datetime.now(timezone.utc)

    # Maximum length session_id (128)
    valid_session = "s" * 128
    meta = BatchMetadata(
        sample_count=1,
        window_start=now,
        window_end=now,
        flush_trigger="studio",
        session_id=valid_session,
    )
    assert meta.session_id == valid_session

    with pytest.raises(ValidationError):
        BatchMetadata(
            sample_count=1,
            window_start=now,
            window_end=now,
            flush_trigger="studio",
            session_id="s" * 129,
        )

    # Maximum length label (64)
    valid_label = "l" * 64
    meta_label = BatchMetadata(
        sample_count=1,
        window_start=now,
        window_end=now,
        flush_trigger="studio",
        label=valid_label,
    )
    assert meta_label.label == valid_label

    with pytest.raises(ValidationError):
        BatchMetadata(
            sample_count=1,
            window_start=now,
            window_end=now,
            flush_trigger="studio",
            label="l" * 65,
        )

    # Disallow extra fields (extra='forbid')
    with pytest.raises(ValidationError):
        BatchMetadata.model_validate({
            "sample_count": 1,
            "window_start": now.isoformat(),
            "window_end": now.isoformat(),
            "flush_trigger": "studio",
            "unknown_field": "invalid",
        })

    # Invalid flush_trigger values
    with pytest.raises(ValidationError):
        BatchMetadata(
            sample_count=1,
            window_start=now,
            window_end=now,
            flush_trigger="invalid_trigger",  # type: ignore[arg-type]
        )

