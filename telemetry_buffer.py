from __future__ import annotations

import collections
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Literal

from schemas import BatchMetadata, Header, Telemetry, TelemetryBatch

LOGGER = logging.getLogger(__name__)

__all__ = ["BatchMetadata", "TelemetryBatch", "TelemetryBuffer"]


class TelemetryBuffer:
    """Accumulates telemetry readings and flushes them as a single MQTT batch.

    The flush happens as soon as one of the two conditions is met:
    - ``max_size`` readings have been accumulated (trigger: ``max_size``);
    - ``flush_interval`` seconds elapsed since the first buffered reading
      (trigger: ``time_interval``).

    When ``continuously_send_telemetry`` is False, nominal periodic flushes
    only recycle local memory (Cloud MQTT publication is skipped).
    Only Studio sessions (trigger="studio" with session_id) publish to MQTT.
    """

    def __init__(
        self,
        device_id: str,
        max_size: int,
        flush_interval: float,
        publish: Callable[[TelemetryBatch], None],
        continuously_send_telemetry: bool = False,
        settings: Any = None,
    ) -> None:
        if max_size < 1:
            raise ValueError("max_size must be >= 1")
        if flush_interval <= 0:
            raise ValueError("flush_interval must be > 0")
        self._device_id = device_id
        self._max_size = max_size
        self._flush_interval = flush_interval
        self._publish = publish
        if settings is not None and hasattr(settings, "continuously_send_telemetry"):
            self._continuously_send_telemetry = bool(settings.continuously_send_telemetry)
            self.settings = settings
        else:
            self._continuously_send_telemetry = continuously_send_telemetry
            self.settings = type("SettingsHolder", (), {"continuously_send_telemetry": self._continuously_send_telemetry})()
        self._lock = threading.Lock()
        self._readings: list[Telemetry] = []
        recent_maxlen = max(max_size, 150)
        self._recent_readings: collections.deque[Telemetry] = collections.deque(maxlen=recent_maxlen)
        self._window_start: datetime | None = None

    @property
    def continuously_send_telemetry(self) -> bool:
        return self._continuously_send_telemetry

    @continuously_send_telemetry.setter
    def continuously_send_telemetry(self, value: bool) -> None:
        self._continuously_send_telemetry = bool(value)
        if hasattr(self.settings, "continuously_send_telemetry"):
            try:
                self.settings.continuously_send_telemetry = bool(value)
            except AttributeError:
                pass

    @property
    def max_size(self) -> int:
        return self._max_size

    @property
    def flush_interval(self) -> float:
        return self._flush_interval

    @property
    def recent_readings(self) -> list[Telemetry]:
        """Returns a snapshot of the most recent readings maintained in memory for local inference."""
        with self._lock:
            return list(self._recent_readings)

    def append(self, telemetry: Telemetry) -> Literal["max_size", None]:
        """Buffer one reading. Returns the trigger when a flush is due."""
        with self._lock:
            if not self._readings:
                self._window_start = datetime.now(timezone.utc)
            self._readings.append(telemetry)
            self._recent_readings.append(telemetry)
            LOGGER.debug("buffer_appended size=%d/%d", len(self._readings), self._max_size)
            if len(self._readings) >= self._max_size:
                return "max_size"
            return None

    def add_reading(self, telemetry: Telemetry) -> Literal["max_size", None]:
        """Alias for append(), recording serial readings in local memory for inference."""
        return self.append(telemetry)

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
        """Publish the buffered readings as a single batch, or retain locally if publishing is disabled."""
        with self._lock:
            if not self._readings:
                return 0
            readings = self._readings
            window_start = self._window_start or readings[0].header.timestamp
            window_end = readings[-1].header.timestamp
            self._readings = []
            self._window_start = None

        should_publish = self._continuously_send_telemetry or (
            trigger == "studio" and session_id is not None
        )
        if not should_publish:
            LOGGER.debug(
                "Nominal telemetry retained locally; Cloud publication skipped (continuously_send_telemetry=False)"
            )
            return 0

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
