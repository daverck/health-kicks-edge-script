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
