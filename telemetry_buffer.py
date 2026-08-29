from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from schemas import Header, Telemetry

LOGGER = logging.getLogger(__name__)


class BatchMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_count: int = Field(ge=1)
    window_start: datetime
    window_end: datetime
    flush_trigger: Literal["max_size", "time_interval", "shutdown"]


class TelemetryBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    header: Header
    metadata: BatchMetadata
    readings: list[Telemetry]


class TelemetryBuffer:
    """Accumulates telemetry readings and flushes them as a single MQTT batch.

    The flush happens as soon as one of the two conditions is met:
    - ``max_size`` readings have been accumulated (trigger: ``max_size``);
    - ``flush_interval`` seconds elapsed since the first buffered reading
      (trigger: ``time_interval``).
    """

    def __init__(
        self,
        device_id: str,
        max_size: int,
        flush_interval: float,
        publish: Callable[[TelemetryBatch], None],
    ) -> None:
        if max_size < 1:
            raise ValueError("max_size must be >= 1")
        if flush_interval <= 0:
            raise ValueError("flush_interval must be > 0")
        self._device_id = device_id
        self._max_size = max_size
        self._flush_interval = flush_interval
        self._publish = publish
        self._lock = threading.Lock()
        self._readings: list[Telemetry] = []
        self._window_start: datetime | None = None

    @property
    def max_size(self) -> int:
        return self._max_size

    @property
    def flush_interval(self) -> float:
        return self._flush_interval

    def append(self, telemetry: Telemetry) -> Literal["max_size", None]:
        """Buffer one reading. Returns the trigger when a flush is due."""
        with self._lock:
            if not self._readings:
                self._window_start = datetime.now(timezone.utc)
            self._readings.append(telemetry)
            LOGGER.debug("buffer_appended size=%d/%d", len(self._readings), self._max_size)
            if len(self._readings) >= self._max_size:
                return "max_size"
            return None

    def due(self) -> bool:
        """Return True when the flush interval has elapsed for the oldest reading."""
        with self._lock:
            if not self._readings or self._window_start is None:
                return False
            elapsed = (datetime.now(timezone.utc) - self._window_start).total_seconds()
            return elapsed >= self._flush_interval

    def flush(self, trigger: Literal["max_size", "time_interval", "shutdown"]) -> int:
        """Publish the buffered readings as a single batch. Returns batch size."""
        with self._lock:
            if not self._readings:
                return 0
            readings = self._readings
            window_start = self._window_start or readings[0].header.timestamp
            window_end = readings[-1].header.timestamp
            self._readings = []
            self._window_start = None
        batch = TelemetryBatch(
            header=Header(device_id=self._device_id, timestamp=datetime.now(timezone.utc)),
            metadata=BatchMetadata(
                sample_count=len(readings),
                window_start=window_start,
                window_end=window_end,
                flush_trigger=trigger,
            ),
            readings=readings,
        )
        self._publish(batch)
        LOGGER.info(
            "Flushing buffer: %d samples sent via MQTT (Trigger: %s)",
            len(readings),
            {"max_size": "Max Size", "time_interval": "Time Interval", "shutdown": "Shutdown"}[trigger],
        )
        return len(readings)
