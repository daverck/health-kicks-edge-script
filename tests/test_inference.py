from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest
from pydantic import ValidationError

from features import FEATURE_NAMES, compute_window_features, extract_feature_vector
from inference_engine import EdgeInferenceEngine, FallDetector
from schemas import DetectionEvent, DetectionMetadata, Header, ImuPayload, Telemetry


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
def test_fall_detector_missing_model_graceful_degradation(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    missing_path = tmp_path / "non_existent_fall_detector.joblib"
    with caplog.at_level("WARNING"):
        detector = FallDetector(model_path=missing_path)

    assert detector.is_loaded is False
    assert "Inference disabled: model file not found" in caplog.text
    # Prediction should return None without crashing
    readings = [{"ax": 0, "ay": 0, "az": 9.8, "gx": 0, "gy": 0, "gz": 0} for _ in range(20)]
    assert detector.predict(readings) is None
    assert detector.evaluate_window(readings, device_id="HK-1") is None


def test_fall_detector_load_real_trained_model_if_present() -> None:
    dev_path = Path(r"F:\Programmation\health-kicks\scripts\models\fall_detector.joblib")
    if not dev_path.exists():
        pytest.skip("Pre-trained model file not present at development location")

    detector = FallDetector(model_path=dev_path)
    assert detector.is_loaded is True
    assert detector.model_name == "HistGradientBoosting"
    assert len(detector.feature_names) == 16
    assert "fall_forward" in detector.classes
    assert detector.window_size_sec == 2.0

    # Test prediction with stationary samples (should be walk, not fall)
    stationary = [{"ax": 0, "ay": 9.81, "az": 0, "gx": 0, "gy": 0, "gz": 0} for _ in range(50)]
    res = detector.predict(stationary)
    assert res is not None
    label, confidence = res
    assert isinstance(label, str)
    assert 0.0 <= confidence <= 1.0
    # A stationary signal is not a fall
    assert detector.evaluate_window(stationary, device_id="HK-1") is None


def test_fall_detector_with_mock_estimator() -> None:
    mock_estimator = MagicMock()
    mock_estimator.predict_proba.return_value = np.array([[0.05, 0.85, 0.10]])

    detector = FallDetector(min_samples=5)
    detector.estimator = mock_estimator
    detector.classes = ["walk", "fall_forward", "stairs"]
    detector.model_name = "MockClassifier"
    detector.window_size_sec = 2.0

    readings = [{"ax": 0, "ay": 9.8, "az": 0, "gx": 0, "gy": 0, "gz": 0} for _ in range(10)]
    prediction = detector.predict(readings)
    assert prediction == ("fall_forward", 0.85)

    event = detector.evaluate_window(readings, device_id="HK-1")
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
def test_fall_detector_debouncing_cooldown() -> None:
    mock_estimator = MagicMock()
    mock_estimator.predict_proba.return_value = np.array([[0.1, 0.9]])

    simulated_clock = 100.0

    detector = FallDetector(
        cooldown_sec=5.0,
        confidence_threshold=0.65,
        min_samples=5,
        time_fn=lambda: simulated_clock,
    )
    detector.estimator = mock_estimator
    detector.classes = ["walk", "fall_forward"]
    detector.model_name = "DebounceTestModel"

    readings = [{"ax": 0, "ay": 9.8, "az": 0, "gx": 0, "gy": 0, "gz": 0} for _ in range(10)]

    # 1. First event triggers normally
    event1 = detector.evaluate_window(readings, device_id="HK-1")
    assert event1 is not None
    assert event1.event_type == "fall_forward"

    # 2. Immediate next evaluation (same timestamp) must be debounced / suppressed
    event2 = detector.evaluate_window(readings, device_id="HK-1")
    assert event2 is None

    # 3. Advancing time to 103.0s (+3.0s < 5.0s cooldown) must still be suppressed
    simulated_clock = 103.0
    event3 = detector.evaluate_window(readings, device_id="HK-1")
    assert event3 is None

    # 4. Advancing time past 5.0s cooldown (e.g. 105.1s) allows a new detection
    simulated_clock = 105.1
    event4 = detector.evaluate_window(readings, device_id="HK-1")
    assert event4 is not None
    assert event4.event_type == "fall_forward"


def test_fall_detector_confidence_threshold_suppression() -> None:
    mock_estimator = MagicMock()
    # Confidence is 0.55, which is below the 0.65 threshold
    mock_estimator.predict_proba.return_value = np.array([[0.45, 0.55]])

    detector = FallDetector(confidence_threshold=0.65, min_samples=5)
    detector.estimator = mock_estimator
    detector.classes = ["walk", "fall_forward"]
    detector.model_name = "ThresholdTest"

    readings = [{"ax": 0, "ay": 9.8, "az": 0, "gx": 0, "gy": 0, "gz": 0} for _ in range(10)]
    event = detector.evaluate_window(readings, device_id="HK-1")
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
    from mqtt_handler import MQTTHandler

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
        fall_topic="healthkicks/v1/HK-1/events/fall",
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
    from config import Settings

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

