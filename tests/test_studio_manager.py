from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, call
import pytest
from pydantic import ValidationError

from schemas import Header, ImuPayload, StudioCaptureConfig, Telemetry, TelemetryBatch
from studio_manager import StudioManager
from telemetry_buffer import TelemetryBuffer


def _sample_telemetry(device_id: str = "HK-1") -> Telemetry:
    return Telemetry(
        header=Header(device_id=device_id, timestamp=datetime.now(timezone.utc)),
        payload=ImuPayload(ax=0.1, ay=0.2, az=9.8, gx=0.01, gy=0.02, gz=0.03),
    )


def test_haptic_countdown_and_flush_sequence() -> None:
    mock_serial = MagicMock()
    mock_buffer = MagicMock()
    mock_buffer.flush.return_value = 10

    # sleep_fn instantané pour test unitaire rapide
    manager = StudioManager(
        serial_handler=mock_serial,
        telemetry_buffer=mock_buffer,
        sleep_fn=lambda _: None,
    )

    config = StudioCaptureConfig(
        session_id="session-uuid-1",
        label="walk",
        duration_sec=5.0,
        pulse_count=3,
        pulse_duration_ms=150,
        pulse_pause_ms=350,
        pulse_intensity=180,
    )

    assert not manager.is_running
    started = manager.start_capture(config)
    assert started is True

    # Attente de la fin du thread
    finished = manager.wait_completion(timeout=2.0)
    assert finished is True
    assert not manager.is_running

    # Vérification que enqueue_haptic a été appelé exactement 3 fois avec (180, 150)
    assert mock_serial.enqueue_haptic.call_count == 3
    mock_serial.enqueue_haptic.assert_has_calls([
        call(180, 150),
        call(180, 150),
        call(180, 150),
    ])

    # Vérification des flushes : d'abord nominal pour vider l'antérieur, puis studio
    assert mock_buffer.flush.call_count == 2
    mock_buffer.flush.assert_has_calls([
        call("time_interval"),
        call("studio", session_id="session-uuid-1", label="walk"),
    ])


def test_concurrency_guard() -> None:
    mock_serial = MagicMock()
    mock_buffer = MagicMock()

    release_sleep = threading.Event()

    def controlled_sleep(seconds: float) -> None:
        release_sleep.wait(timeout=2.0)

    manager = StudioManager(
        serial_handler=mock_serial,
        telemetry_buffer=mock_buffer,
        sleep_fn=controlled_sleep,
    )

    config1 = StudioCaptureConfig(session_id="sess-1", label="walk")
    config2 = StudioCaptureConfig(session_id="sess-2", label="run")

    started1 = manager.start_capture(config1)
    assert started1 is True
    assert manager.is_running is True

    # Tentative d'une deuxième session alors que la première tourne
    started2 = manager.start_capture(config2)
    assert started2 is False

    # Libération du thread
    release_sleep.set()
    manager.wait_completion(timeout=2.0)
    assert manager.is_running is False

    # Une nouvelle session peut maintenant démarrer
    started3 = manager.start_capture(config2)
    assert started3 is True
    manager.wait_completion(timeout=2.0)
    assert manager.is_running is False


def test_active_session_metadata_lifecycle() -> None:
    mock_serial = MagicMock()
    mock_buffer = MagicMock()

    step_event = threading.Event()
    unblock_recording = threading.Event()

    def step_sleep(seconds: float) -> None:
        if seconds > 1.0:  # attente pendant la capture duration_sec
            step_event.set()
            unblock_recording.wait(timeout=2.0)

    manager = StudioManager(
        serial_handler=mock_serial,
        telemetry_buffer=mock_buffer,
        sleep_fn=step_sleep,
    )

    assert manager.get_active_session_metadata() == (None, None)
    assert manager.is_recording is False

    config = StudioCaptureConfig(session_id="meta-session", label="stairs", duration_sec=5.0)
    manager.start_capture(config)

    # Attente que l'orchestrateur atteigne la phase d'enregistrement
    step_event.wait(timeout=2.0)
    assert manager.is_recording is True
    assert manager.get_active_session_metadata() == ("meta-session", "stairs")

    # Débloque la fin de session
    unblock_recording.set()
    manager.wait_completion(timeout=2.0)

    assert manager.is_recording is False
    assert manager.get_active_session_metadata() == (None, None)


def test_studio_capture_with_telemetry_buffer_integration() -> None:
    mock_serial = MagicMock()
    published_batches: list[TelemetryBatch] = []

    buffer = TelemetryBuffer(
        "HK-1", max_size=100, flush_interval=10.0, publish=published_batches.append, continuously_send_telemetry=True
    )

    # Points ajoutés avant le studio
    buffer.append(_sample_telemetry())
    buffer.append(_sample_telemetry())

    def record_during_sleep(seconds: float) -> None:
        if seconds >= 1.0:
            # Ajout de 3 points pendant la fenêtre de capture studio
            buffer.append(_sample_telemetry())
            buffer.append(_sample_telemetry())
            buffer.append(_sample_telemetry())

    manager = StudioManager(
        serial_handler=mock_serial,
        telemetry_buffer=buffer,
        sleep_fn=record_during_sleep,
    )

    config = StudioCaptureConfig(session_id="real-session-42", label="jump", duration_sec=2.0)
    manager.start_capture(config)
    manager.wait_completion(timeout=2.0)

    assert len(published_batches) == 2

    # Premier batch : vidage nominal des 2 points initiaux
    nominal_batch = published_batches[0]
    assert nominal_batch.metadata.flush_trigger == "time_interval"
    assert nominal_batch.metadata.session_id is None
    assert nominal_batch.metadata.label is None
    assert nominal_batch.metadata.sample_count == 2

    # Deuxième batch : capture studio des 3 points pendant la fenêtre
    studio_batch = published_batches[1]
    assert studio_batch.metadata.flush_trigger == "studio"
    assert studio_batch.metadata.session_id == "real-session-42"
    assert studio_batch.metadata.label == "jump"
    assert studio_batch.metadata.sample_count == 3


def test_studio_manager_with_buffer_when_continuous_send_false() -> None:
    mock_serial = MagicMock()
    published_batches: list[TelemetryBatch] = []

    buffer = TelemetryBuffer(
        "HK-1", max_size=100, flush_interval=10.0, publish=published_batches.append, continuously_send_telemetry=False
    )

    # Points nominaux ajoutés avant le studio
    buffer.append(_sample_telemetry())
    buffer.append(_sample_telemetry())

    def record_during_sleep(seconds: float) -> None:
        if seconds >= 1.0:
            # Points pendant la capture studio
            buffer.append(_sample_telemetry())
            buffer.append(_sample_telemetry())
            buffer.append(_sample_telemetry())

    manager = StudioManager(
        serial_handler=mock_serial,
        telemetry_buffer=buffer,
        sleep_fn=record_during_sleep,
    )

    config = StudioCaptureConfig(session_id="studio-only-42", label="run", duration_sec=2.0)
    manager.start_capture(config)
    manager.wait_completion(timeout=2.0)

    # Seul le batch studio doit avoir été publié sur le réseau !
    assert len(published_batches) == 1
    studio_batch = published_batches[0]
    assert studio_batch.metadata.flush_trigger == "studio"
    assert studio_batch.metadata.session_id == "studio-only-42"
    assert studio_batch.metadata.label == "run"
    assert studio_batch.metadata.sample_count == 3


def test_studio_manager_dynamic_duration() -> None:
    mock_serial = MagicMock()
    mock_buffer = MagicMock()
    mock_buffer.flush.return_value = 10

    durations_slept: list[float] = []

    def record_sleep(seconds: float) -> None:
        durations_slept.append(seconds)

    manager = StudioManager(
        serial_handler=mock_serial,
        telemetry_buffer=mock_buffer,
        sleep_fn=record_sleep,
    )

    # Test avec duration_sec=3.5
    config_3_5 = StudioCaptureConfig(
        session_id="sess-3-5",
        label="walk",
        duration_sec=3.5,
    )
    manager.execute_session(config_3_5)
    manager.wait_completion(timeout=2.0)

    # La durée de sommeil pendant la fenêtre d'enregistrement doit valoir 3.5s
    assert durations_slept[-1] == 3.5
    mock_buffer.flush.assert_called_with("studio", session_id="sess-3-5", label="walk")

    # Test avec duration_sec=6.0
    durations_slept.clear()
    config_6_0 = StudioCaptureConfig(
        session_id="sess-6-0",
        label="sprint",
        duration_sec=6.0,
    )
    manager.execute_session(config_6_0)
    manager.wait_completion(timeout=2.0)

    assert durations_slept[-1] == 6.0
    mock_buffer.flush.assert_called_with("studio", session_id="sess-6-0", label="sprint")


def test_studio_capture_config_validation() -> None:
    # Configuration par défaut valide
    valid = StudioCaptureConfig(session_id="id-1", label="walk")
    assert valid.duration_sec == 5.0
    assert valid.pulse_count == 3
    assert valid.pulse_duration_ms == 150
    assert valid.pulse_pause_ms == 350
    assert valid.pulse_intensity == 180

    # duration_sec hors limites (1.0 à 30.0)
    with pytest.raises(ValidationError):
        StudioCaptureConfig(session_id="id", label="lbl", duration_sec=0.5)
    with pytest.raises(ValidationError):
        StudioCaptureConfig(session_id="id", label="lbl", duration_sec=35.0)

    # pulse_count hors limites (1 à 5)
    with pytest.raises(ValidationError):
        StudioCaptureConfig(session_id="id", label="lbl", pulse_count=0)
    with pytest.raises(ValidationError):
        StudioCaptureConfig(session_id="id", label="lbl", pulse_count=6)

    # pulse_intensity hors limites (50 à 255)
    with pytest.raises(ValidationError):
        StudioCaptureConfig(session_id="id", label="lbl", pulse_intensity=40)
    with pytest.raises(ValidationError):
        StudioCaptureConfig(session_id="id", label="lbl", pulse_intensity=260)

    # session_id et label vides
    with pytest.raises(ValidationError):
        StudioCaptureConfig(session_id="", label="walk")
    with pytest.raises(ValidationError):
        StudioCaptureConfig(session_id="id", label="")


def test_studio_cancel() -> None:
    mock_serial = MagicMock()
    mock_buffer = MagicMock()

    manager = StudioManager(
        serial_handler=mock_serial,
        telemetry_buffer=mock_buffer,
    )

    config = StudioCaptureConfig(session_id="cancel-sess", label="walk", duration_sec=10.0)
    manager.start_capture(config)
    assert manager.is_running is True

    # Annulation immédiate
    manager.cancel()
    finished = manager.wait_completion(timeout=2.0)
    assert finished is True
    assert manager.is_running is False
