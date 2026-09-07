from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Callable, Literal

from schemas import BatchMetadata, Header, Telemetry, TelemetryBatch

LOGGER = logging.getLogger(__name__)

__all__ = ["BatchMetadata", "TelemetryBatch", "TelemetryBuffer"]


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

    def flush(
        self,
        trigger: Literal["max_size", "time_interval", "shutdown", "studio"],
        session_id: str | None = None,
        label: str | None = None,
    ) -> int:
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
                session_id=session_id,
                label=label,
            ),
            readings=readings,
        )
        self._publish(batch)
        trigger_names = {
            "max_size": "Max Size",
            "time_interval": "Time Interval",
            "shutdown": "Shutdown",
            "studio": "Studio",
        }
        LOGGER.info(
            "Flushing buffer: %d samples sent via MQTT (Trigger: %s)",
            len(readings),
            trigger_names.get(trigger, trigger),
        )
        return len(readings)
