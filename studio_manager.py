from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from schemas import StudioCaptureConfig

LOGGER = logging.getLogger(__name__)


class StudioManager:
    """Manages the lifecycle of a local Studio capture session:
    1. Plays a sensory countdown via haptic pulses (non-blocking for serial reading).
    2. Flushes any pre-existing nominal telemetry.
    3. Opens an IMU capture window of duration_sec, associating telemetry with session_id & label.
    4. Triggers immediate buffer flush with 'studio' trigger and metadata upon completion.
    """

    def __init__(
        self,
        serial_handler: Any,
        telemetry_buffer: Any,
        stop_event: threading.Event | None = None,
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        self._serial_handler = serial_handler
        self._telemetry_buffer = telemetry_buffer
        self._stop_event = stop_event
        self._sleep_fn = sleep_fn

        self._lock = threading.Lock()
        self._cancel_event = threading.Event()
        self._is_running: bool = False
        self._active_session_id: str | None = None
        self._active_label: str | None = None
        self._session_deadline: float | None = None
        self._thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        """Returns True if a studio session is currently active (countdown or recording)."""
        with self._lock:
            return self._is_running

    @property
    def is_recording(self) -> bool:
        """Returns True if the studio session is actively recording IMU measurements."""
        with self._lock:
            return self._active_session_id is not None

    @property
    def active_session_id(self) -> str | None:
        with self._lock:
            return self._active_session_id

    @property
    def active_label(self) -> str | None:
        with self._lock:
            return self._active_label

    @property
    def session_deadline(self) -> float | None:
        with self._lock:
            return self._session_deadline

    def get_active_session_metadata(self) -> tuple[str | None, str | None]:
        """Returns (active_session_id, active_label) thread-safely."""
        with self._lock:
            return self._active_session_id, self._active_label

    def start_capture(self, config: StudioCaptureConfig) -> bool:
        """Launches the studio sequence in a dedicated background thread.
        Returns False if a studio session is already in progress (concurrency guard).
        """
        with self._lock:
            if self._is_running:
                LOGGER.warning(
                    "studio_session_rejected reason=already_running active_session=%s",
                    self._active_session_id,
                )
                return False
            self._is_running = True
            self._active_session_id = None
            self._active_label = None
            self._session_deadline = None
            self._cancel_event.clear()

        self._thread = threading.Thread(
            target=self._run_orchestrator,
            args=(config,),
            name="studio-orchestrator",
            daemon=True,
        )
        self._thread.start()
        return True

    def execute_session(self, config: StudioCaptureConfig) -> bool:
        """Executes a studio capture session (alias for start_capture)."""
        return self.start_capture(config)

    def cancel(self) -> None:
        """Requests cancellation of an ongoing studio session."""
        self._cancel_event.set()

    def wait_completion(self, timeout: float | None = None) -> bool:
        """Waits for the background orchestrator thread to finish.
        Returns True if finished, False if timed out.
        """
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
            return not thread.is_alive()
        return True

    def _sleep(self, seconds: float) -> bool:
        """Sleep helper supporting cancellation and custom sleep_fn."""
        if self._sleep_fn is not None:
            self._sleep_fn(seconds)
            return not self._cancel_event.is_set()

        # Responsive sleep checking cancellation / stop_event
        deadline = time.monotonic() + seconds
        while True:
            if self._cancel_event.is_set():
                return False
            if self._stop_event is not None and self._stop_event.is_set():
                return False
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return True
            time.sleep(min(remaining, 0.05))

    def _run_orchestrator(self, config: StudioCaptureConfig) -> None:
        LOGGER.info(
            "studio_sequence_started session_id=%s label=%s pulses=%d duration=%.1fs",
            config.session_id,
            config.label,
            config.pulse_count,
            config.duration_sec,
        )
        sid = config.session_id
        lbl = config.label

        try:
            # a. Execute haptic pulses (sensory countdown)
            pulse_interval = (config.pulse_duration_ms + config.pulse_pause_ms) / 1000.0
            for pulse_idx in range(1, config.pulse_count + 1):
                if self._cancel_event.is_set() or (self._stop_event and self._stop_event.is_set()):
                    LOGGER.info("studio_aborted_during_countdown")
                    return

                self._serial_handler.enqueue_haptic(
                    config.pulse_intensity,
                    config.pulse_duration_ms,
                )
                LOGGER.debug("studio_pulse_enqueued pulse=%d/%d", pulse_idx, config.pulse_count)

                if not self._sleep(pulse_interval):
                    LOGGER.info("studio_aborted_during_pulse_delay")
                    return

            if self._cancel_event.is_set() or (self._stop_event and self._stop_event.is_set()):
                LOGGER.info("studio_aborted_before_recording")
                return

            # Flush any nominal telemetry accumulated prior to recording start
            self._telemetry_buffer.flush("time_interval")

            # b. Start recording window
            deadline = time.monotonic() + config.duration_sec
            with self._lock:
                self._active_session_id = sid
                self._active_label = lbl
                self._session_deadline = deadline

            LOGGER.info(
                "studio_recording_started session_id=%s label=%s duration=%.1fs deadline=%.2f",
                sid,
                lbl,
                config.duration_sec,
                deadline,
            )

            # c. Wait for capture window to complete
            self._sleep(config.duration_sec)

        finally:
            # d. Finalize session
            with self._lock:
                self._active_session_id = None
                self._active_label = None
                self._session_deadline = None
                self._is_running = False

            flushed_count = self._telemetry_buffer.flush("studio", session_id=sid, label=lbl)
            LOGGER.info(
                "studio_recording_finished session_id=%s label=%s samples_flushed=%d",
                sid,
                lbl,
                flushed_count,
            )
