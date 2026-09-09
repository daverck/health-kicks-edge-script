from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Header(StrictModel):
    device_id: str = Field(min_length=1)
    schema_version: Literal["1.0"] = "1.0"
    timestamp: datetime
    msg_id: UUID = Field(default_factory=uuid4)

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamp must include a timezone")
        return value.astimezone(timezone.utc)


class ImuPayload(StrictModel):
    ax: float
    ay: float
    az: float
    gx: float
    gy: float
    gz: float


class Telemetry(StrictModel):
    header: Header
    payload: ImuPayload


class FallPayload(ImuPayload):
    anomaly_score: float
    detection_source: Literal["edge_ai"] = "edge_ai"


class FallEvent(StrictModel):
    header: Header
    payload: FallPayload


class HapticCommand(StrictModel):
    intensity: int = Field(ge=0, le=255)
    duration_ms: int = Field(ge=50, le=10000)


class DeviceStatusPayload(StrictModel):
    state: Literal["online", "offline"]
    reason: str | None = None
    uptime: int | None = Field(default=None, ge=0)
    cpu_temp: float | None = None


class DeviceStatus(StrictModel):
    header: Header
    payload: DeviceStatusPayload


class BatchMetadata(StrictModel):
    sample_count: int = Field(ge=1)
    window_start: datetime
    window_end: datetime
    flush_trigger: Literal["max_size", "time_interval", "shutdown", "studio"]
    session_id: str | None = Field(default=None, max_length=128)
    label: str | None = Field(default=None, max_length=64)


class TelemetryBatch(StrictModel):
    header: Header
    metadata: BatchMetadata
    readings: list[Telemetry]


class StudioCaptureConfig(StrictModel):
    session_id: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=64)
    duration_sec: float = Field(default=5.0, ge=1.0, le=30.0)
    pulse_count: int = Field(default=3, ge=1, le=5)
    pulse_duration_ms: int = Field(default=150, ge=50, le=1000)
    pulse_pause_ms: int = Field(default=350, ge=100, le=1000)
    pulse_intensity: int = Field(default=180, ge=50, le=255)


class DetectionMetadata(StrictModel):
    model_name: str = Field(min_length=1)
    window_size_sec: float = Field(gt=0.0)


class DetectionEvent(StrictModel):
    device_id: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    timestamp: int = Field(ge=0)
    metadata: DetectionMetadata


