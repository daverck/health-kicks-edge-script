from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest
from pydantic import ValidationError

from healthkicks_edge.activity_classifier import (
    ActivityClassifier,
    MIN_FALL_IMPACT_THRESHOLD,
    compute_window_biomechanics,
)
from healthkicks_edge.features import FEATURE_NAMES, compute_window_features, extract_feature_vector
from healthkicks_edge.schemas import DetectionEvent, DetectionMetadata, Header, ImuPayload, Telemetry


def _create_sample_telemetry(
    device_id: str = "HK-1",
    ax: float = 0.0,
    ay: float = 0.0,
    az: float = 9.81,
    gx: float = 0.0,
    gy: float = 0.0,
    gz: float = 0.0,
) -> Telemetry:
    return Telemetry(
        header=Header(device_id=device_id, timestamp=datetime.now(timezone.utc)),
        payload=ImuPayload(ax=ax, ay=ay, az=az, gx=gx, gy=gy, gz=gz),
    )


# -----------------------------------------------------------------------------
# 1. Feature Extraction Tests
# -----------------------------------------------------------------------------
def test_compute_window_features_keys_and_types() -> None:
    readings = [
        {"ax": 0.1, "ay": 0.2, "az": 9.8, "gx": 0.01, "gy": 0.02, "gz": 0.03}
        for _ in range(20)
    ]
    features = compute_window_features(readings)

    assert isinstance(features, dict)
    assert len(features) == 16
    for key in FEATURE_NAMES:
        assert key in features
        assert isinstance(features[key], float)
        assert not np.isnan(features[key])


def test_compute_window_features_stationary() -> None:
    # 50 points with static gravity along az
    readings = [
        {"ax": 0.0, "ay": 0.0, "az": 9.81, "gx": 0.0, "gy": 0.0, "gz": 0.0}
        for _ in range(50)
    ]
    features = compute_window_features(readings)

    assert pytest.approx(features["acc_mag_max"], 1e-4) == 9.81
    assert pytest.approx(features["acc_mag_min"], 1e-4) == 9.81
    assert pytest.approx(features["acc_mag_mean"], 1e-4) == 9.81
    assert pytest.approx(features["acc_mag_std"], 1e-4) == 0.0
    assert pytest.approx(features["acc_mag_peak_to_peak"], 1e-4) == 0.0
    assert pytest.approx(features["gyro_mag_mean"], 1e-4) == 0.0
    assert pytest.approx(features["acc_energy"], 1e-4) == 9.81**2
    assert pytest.approx(features["gyro_energy"], 1e-4) == 0.0


def test_compute_window_features_telemetry_objects() -> None:
    readings = [
        _create_sample_telemetry(
            ax=float(i) * 0.1,
            ay=9.8,
            az=0.0,
            gx=0.05,
            gy=-0.05,
            gz=0.0,
        )
        for i in range(15)
    ]
    features = compute_window_features(readings)
    assert len(features) == 16
    assert features["acc_mag_mean"] > 9.8


def test_compute_window_features_empty_raises() -> None:
    with pytest.raises(ValueError, match="empty IMU window"):
        compute_window_features([])


def test_extract_feature_vector_shape() -> None:
    readings = [{"ax": 0, "ay": 9.8, "az": 0, "gx": 0, "gy": 0, "gz": 0} for _ in range(10)]
    vector = extract_feature_vector(readings)
    assert isinstance(vector, np.ndarray)
    assert vector.shape == (1, 16)


# -----------------------------------------------------------------------------
# 2. Inference Engine & Model Loading Tests
# -----------------------------------------------------------------------------
def test_activity_classifier_missing_model_graceful_degradation(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    missing_path = tmp_path / "non_existent_activity_classifier.joblib"
    with caplog.at_level("WARNING"):
        classifier = ActivityClassifier(model_path=missing_path)

    assert classifier.is_loaded is False
    assert "Inference disabled: model file not found" in caplog.text
    # Prediction should return None without crashing
    readings = [{"ax": 0, "ay": 0, "az": 9.8, "gx": 0, "gy": 0, "gz": 0} for _ in range(20)]
    assert classifier.predict(readings) is None
    assert classifier.evaluate_window(readings, device_id="HK-1") is None


def test_activity_classifier_load_real_trained_model_if_present() -> None:
    repo_path = Path(__file__).resolve().parent.parent / "models" / "activity_classifier.joblib"
    if not repo_path.exists():
        pytest.skip("Pre-trained model file not present")

    classifier = ActivityClassifier(model_path=repo_path)
    assert classifier.is_loaded is True
    assert classifier.model_name == "HistGradientBoosting"
    assert len(classifier.feature_names) == 16
    assert "fall_forward" in classifier.classes
    assert classifier.window_size_sec == 2.0

    # Test prediction with stationary samples (should be walk, not fall)
    stationary = [{"ax": 0, "ay": 9.81, "az": 0, "gx": 0, "gy": 0, "gz": 0} for _ in range(50)]
    res = classifier.predict(stationary)
    assert res is not None
    label, confidence = res
    assert isinstance(label, str)
    assert 0.0 <= confidence <= 1.0
    # A stationary signal is not a fall
    assert classifier.evaluate_window(stationary, device_id="HK-1") is None


def test_activity_classifier_with_mock_estimator() -> None:
    mock_estimator = MagicMock()
    mock_estimator.predict_proba.return_value = np.array([[0.05, 0.85, 0.10]])

    classifier = ActivityClassifier(min_samples=5)
    classifier.estimator = mock_estimator
    classifier.classes = ["walk", "fall_forward", "stairs"]
    classifier.model_name = "MockClassifier"
    classifier.window_size_sec = 2.0

    # Readings simulating a fall with impact exceeding the 18.0 m/s² threshold
    readings = [{"ax": 0, "ay": 22.0, "az": 0, "gx": 0, "gy": 0, "gz": 0} for _ in range(10)]
    prediction = classifier.predict(readings)
    assert prediction == ("fall_forward", 0.85)

    event = classifier.evaluate_window(readings, device_id="HK-1")
    assert event is not None
    assert event.device_id == "HK-1"
    assert event.event_type == "fall_forward"
    assert event.confidence == 0.85
    assert event.metadata.model_name == "MockClassifier"
    assert event.metadata.window_size_sec == 2.0
    assert event.timestamp > 0


# -----------------------------------------------------------------------------
# 3. Debounce / Cooldown Logic Tests
# -----------------------------------------------------------------------------
def test_activity_classifier_debouncing_cooldown() -> None:
    mock_estimator = MagicMock()
    mock_estimator.predict_proba.return_value = np.array([[0.1, 0.9]])

    simulated_clock = 100.0

    classifier = ActivityClassifier(
        cooldown_sec=5.0,
        confidence_threshold=0.65,
        min_samples=5,
        time_fn=lambda: simulated_clock,
    )
    classifier.estimator = mock_estimator
    classifier.classes = ["walk", "fall_forward"]
    classifier.model_name = "DebounceTestModel"

    # Impact reading exceeding 18.0 m/s²
    readings = [{"ax": 0, "ay": 22.0, "az": 0, "gx": 0, "gy": 0, "gz": 0} for _ in range(10)]

    # 1. First event triggers normally
    event1 = classifier.evaluate_window(readings, device_id="HK-1")
    assert event1 is not None
    assert event1.event_type == "fall_forward"

    # 2. Immediate next evaluation (same timestamp) must be debounced / suppressed
    event2 = classifier.evaluate_window(readings, device_id="HK-1")
    assert event2 is None

    # 3. Advancing time to 103.0s (+3.0s < 5.0s cooldown) must still be suppressed
    simulated_clock = 103.0
    event3 = classifier.evaluate_window(readings, device_id="HK-1")
    assert event3 is None

    # 4. Advancing time past 5.0s cooldown (e.g. 105.1s) allows a new detection
    simulated_clock = 105.1
    event4 = classifier.evaluate_window(readings, device_id="HK-1")
    assert event4 is not None
    assert event4.event_type == "fall_forward"


def test_activity_classifier_confidence_threshold_suppression() -> None:
    mock_estimator = MagicMock()
    # Confidence is 0.55, which is below the 0.65 threshold
    mock_estimator.predict_proba.return_value = np.array([[0.45, 0.55]])

    classifier = ActivityClassifier(confidence_threshold=0.65, min_samples=5)
    classifier.estimator = mock_estimator
    classifier.classes = ["walk", "fall_forward"]
    classifier.model_name = "ThresholdTest"

    # Impact present (peak_acc > 18.0) but confidence is below threshold
    readings = [{"ax": 0, "ay": 22.0, "az": 0, "gx": 0, "gy": 0, "gz": 0} for _ in range(10)]
    event = classifier.evaluate_window(readings, device_id="HK-1")
    assert event is None


def test_detection_event_schema_strictness() -> None:
    valid_event = DetectionEvent(
        device_id="HK-1",
        event_type="fall_forward",
        confidence=0.88,
        timestamp=1773037874000,
        metadata=DetectionMetadata(model_name="HistGradientBoosting", window_size_sec=2.0),
    )
    assert valid_event.device_id == "HK-1"
    assert valid_event.metadata.window_size_sec == 2.0

    # Extra fields must be forbidden
    with pytest.raises(ValidationError):
        DetectionEvent(
            device_id="HK-1",
            event_type="fall_forward",
            confidence=0.88,
            timestamp=1773037874000,
            metadata=DetectionMetadata(model_name="HistGradientBoosting", window_size_sec=2.0),
            extra_field="invalid",
        )


def test_mqtt_publish_detection() -> None:
    from healthkicks_edge.mqtt_handler import MQTTHandler

    published_messages: list[tuple[str, str, int]] = []

    class FakeClient:
        def publish(self, topic: str, payload: str, qos: int, retain: bool = False):
            published_messages.append((topic, payload, qos))
            return type("Result", (), {"rc": 0})()

    handler = MQTTHandler(
        host="localhost",
        port=1883,
        client_id="test-client",
        username=None,
        password=None,
        device_id="HK-1",
        telemetry_topic="healthkicks/v1/HK-1/telemetry/raw",
        command_topic="healthkicks/v1/HK-1/commands/haptic",
        status_topic="healthkicks/v1/HK-1/status",
        ack_topic="healthkicks/v1/HK-1/commands/ack",
        heartbeat_interval=30,
        on_haptic_command=lambda _: None,
        detection_topic="healthkicks/v1/HK-1/events/detection",
    )
    handler.client = FakeClient()  # type: ignore[assignment]

    event = DetectionEvent(
        device_id="HK-1",
        event_type="fall_lateral",
        confidence=0.91,
        timestamp=1773037874000,
        metadata=DetectionMetadata(model_name="HistGradientBoosting", window_size_sec=2.0),
    )
    handler.publish_detection(event)

    assert len(published_messages) == 1
    topic, payload_str, qos = published_messages[0]
    assert topic == "healthkicks/v1/HK-1/events/detection"
    assert qos == 1
    assert "fall_lateral" in payload_str
    assert "0.91" in payload_str


def test_settings_detection_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from healthkicks_edge.config import Settings

    monkeypatch.setenv("EDGE_DEVICE_ID", "HK-99")
    monkeypatch.setenv("EDGE_DETECTION_TOPIC", "custom/detection/topic")
    monkeypatch.setenv("EDGE_INFERENCE_INTERVAL_SEC", "0.5")
    monkeypatch.setenv("EDGE_CONFIDENCE_THRESHOLD", "0.75")
    monkeypatch.setenv("EDGE_DETECTION_COOLDOWN_SEC", "10.0")

    settings = Settings.from_env()
    assert settings.device_id == "HK-99"
    assert settings.detection_topic == "custom/detection/topic"
    assert settings.inference_interval_seconds == 0.5
    assert settings.confidence_threshold == 0.75
    assert settings.detection_cooldown_seconds == 10.0


def test_settings_resolves_repo_model_path(monkeypatch: pytest.MonkeyPatch) -> None:
    from healthkicks_edge.config import Settings

    monkeypatch.delenv("EDGE_MODEL_PATH", raising=False)
    settings = Settings.from_env()

    # The bundled model in repo should be resolved if /opt default doesn't exist
    repo_model = Path(__file__).resolve().parent.parent / "models" / "activity_classifier.joblib"
    default_model = Path("/opt/healthkicks_edge/models/activity_classifier.joblib")

    if default_model.exists():
        assert settings.model_path == str(default_model)
    elif repo_model.exists():
        assert Path(settings.model_path).resolve() == repo_model.resolve()


# -----------------------------------------------------------------------------
# 4. Biomechanical Guard Unit Tests
# -----------------------------------------------------------------------------
def test_biomechanical_guard_suppresses_false_fall_on_stationary_window(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Simulates a stationary window (ax=0, ay=9.8, az=0 without noise).

    Verifies that NO fall alert is raised, even if the underlying statistical
    model returns an erroneous fall prediction with high confidence (> 85%).
    """
    mock_estimator = MagicMock()
    # Statistical model erroneously predicting fall_forward with 95% confidence
    mock_estimator.predict_proba.return_value = np.array([[0.05, 0.95]])

    classifier = ActivityClassifier(min_samples=5, min_impact_threshold=18.0)
    classifier.estimator = mock_estimator
    classifier.classes = ["walk", "fall_forward"]
    classifier.model_name = "MockBiomechanicalGuardTest"

    # Pure stationary window without impact (peak_acc = 9.80 < 18.0 m/s²)
    stationary_readings = [
        {"ax": 0.0, "ay": 9.8, "az": 0.0, "gx": 0.0, "gy": 0.0, "gz": 0.0}
        for _ in range(32)
    ]

    with caplog.at_level("DEBUG"):
        prediction = classifier.predict(stationary_readings)
        event = classifier.evaluate_window(stationary_readings, device_id="HK-1")

    # 1. Prediction must be overridden to 'idle'
    assert prediction == ("idle", 1.0)
    # 2. No detection event generated (neither MQTT nor haptic)
    assert event is None
    # 3. Debug log message emitted with peak_acc
    assert "Suppressed false fall detection (peak_acc=9.80 < threshold)" in caplog.text


def test_biomechanical_guard_allows_fall_when_impact_threshold_exceeded() -> None:
    """Verifies that an authentic fall with impact (peak_acc >= 18.0 m/s²) is confirmed."""
    mock_estimator = MagicMock()
    mock_estimator.predict_proba.return_value = np.array([[0.10, 0.90]])

    classifier = ActivityClassifier(min_samples=5, min_impact_threshold=18.0)
    classifier.estimator = mock_estimator
    classifier.classes = ["walk", "fall_lateral"]
    classifier.model_name = "MockFallImpactTest"

    # Pre-fall and post-fall readings with an impact spike exceeding 18.0 m/s²
    readings = [
        {"ax": 0.0, "ay": 9.8, "az": 0.0, "gx": 0.0, "gy": 0.0, "gz": 0.0}
        for _ in range(20)
    ]
    readings[10] = {"ax": 12.0, "ay": 21.0, "az": 5.0, "gx": 1.2, "gy": 2.5, "gz": 0.5}

    prediction = classifier.predict(readings)
    assert prediction == ("fall_lateral", 0.90)

    event = classifier.evaluate_window(readings, device_id="HK-1")
    assert event is not None
    assert event.event_type == "fall_lateral"
    assert event.confidence == 0.90


def test_compute_window_biomechanics_formula() -> None:
    readings = [
        {"ax": 3.0, "ay": 4.0, "az": 0.0, "gx": 0.0, "gy": 0.0, "gz": 0.0},  # mag = 5.0
        {"ax": 0.0, "ay": 12.0, "az": 5.0, "gx": 0.0, "gy": 0.0, "gz": 0.0}, # mag = 13.0
    ]
    peak_acc, energy = compute_window_biomechanics(readings)
    assert peak_acc == 13.0
    # energy = (5^2 + 13^2) / 2 = (25 + 169) / 2 = 97.0
    assert pytest.approx(energy, 1e-4) == 97.0


def test_settings_min_fall_impact_threshold_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from healthkicks_edge.config import Settings

    monkeypatch.setenv("EDGE_MIN_FALL_IMPACT_THRESHOLD", "22.5")
    settings = Settings.from_env()
    assert settings.min_fall_impact_threshold == 22.5



