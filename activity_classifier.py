from __future__ import annotations

import logging
import os
import threading
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

import joblib
import numpy as np

from features import FEATURE_NAMES, extract_feature_vector
from schemas import DetectionEvent, DetectionMetadata

LOGGER = logging.getLogger(__name__)


class ActivityClassifier:
    """Edge inference engine for real-time IMU activity and fall classification.

    Loads a trained joblib model, processes rolling IMU windows, extracts
    16 biomechanical features, and generates timestamped detection events
    with anti-spam debouncing.
    """

    def __init__(
        self,
        model_path: str | Path | None = None,
        cooldown_sec: float = 5.0,
        confidence_threshold: float = 0.65,
        min_samples: int = 10,
        time_fn: Callable[[], float] | None = None,
    ) -> None:
        self.cooldown_sec = max(0.0, float(cooldown_sec))
        self.confidence_threshold = max(0.0, min(1.0, float(confidence_threshold)))
        self.min_samples = max(1, int(min_samples))
        self._time_fn = time_fn or time.monotonic

        self._lock = threading.Lock()
        self._last_detection_time: float = -1e9
        self.estimator: Any = None
        self.model_name: str = "Unknown"
        self.feature_names: list[str] = list(FEATURE_NAMES)
        self.classes: list[str] = []
        self.window_size_sec: float = 2.0
        self.model_path: str | None = None

        if model_path:
            self.load_model(model_path)

    @property
    def is_loaded(self) -> bool:
        """Returns True if the ML model is successfully loaded and ready for inference."""
        with self._lock:
            return self.estimator is not None

    def load_model(self, model_path: str | Path | None) -> bool:
        """Loads a model package from a joblib file.

        Returns True on success, False if file is missing or invalid.
        """
        if not model_path:
            LOGGER.warning("Inference disabled: model file not specified")
            return False

        path_str = str(model_path)
        if not os.path.exists(path_str):
            LOGGER.warning("Inference disabled: model file not found at %s", path_str)
            return False

        try:
            package = joblib.load(path_str)
            with self._lock:
                self.model_path = path_str
                if isinstance(package, dict) and "estimator" in package:
                    self.estimator = package["estimator"]
                    self.model_name = str(package.get("model_name", type(self.estimator).__name__))
                    self.feature_names = list(package.get("feature_names", FEATURE_NAMES))
                    raw_classes = package.get("classes", [])
                    self.classes = [str(c) for c in raw_classes]
                    self.window_size_sec = float(package.get("window_size_sec", 2.0))
                else:
                    self.estimator = package
                    self.model_name = type(package).__name__
                    self.feature_names = list(FEATURE_NAMES)
                    self.classes = [str(c) for c in getattr(package, "classes_", [])]
                    self.window_size_sec = 2.0

            LOGGER.info(
                "activity_classifier_loaded model=%s features=%d classes=%s window_size=%.1fs path=%s",
                self.model_name,
                len(self.feature_names),
                self.classes,
                self.window_size_sec,
                path_str,
            )
            return True
        except Exception as exc:
            LOGGER.error("activity_classifier_load_failed path=%s error=%s", path_str, exc)
            return False

    def predict(
        self, readings: Sequence[Any] | np.ndarray
    ) -> tuple[str, float] | None:
        """Evaluates an IMU window and returns (predicted_label, confidence).

        Returns None if model is not loaded or readings window is too small.
        """
        if not self.is_loaded or len(readings) < self.min_samples:
            return None

        try:
            X = extract_feature_vector(readings, self.feature_names)
        except Exception as err:
            LOGGER.debug("feature_extraction_failed error=%s", err)
            return None

        with self._lock:
            estimator = self.estimator
            classes = self.classes

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                if hasattr(estimator, "predict_proba"):
                    probas = estimator.predict_proba(X)[0]
                    best_idx = int(np.argmax(probas))
                    if classes and best_idx < len(classes):
                        label = str(classes[best_idx])
                    else:
                        label = str(estimator.predict(X)[0])
                    confidence = float(probas[best_idx])
                else:
                    label = str(estimator.predict(X)[0])
                    confidence = 1.0
            return label, confidence
        except Exception as exc:
            LOGGER.error("inference_prediction_error error=%s", exc)
            return None

    def evaluate_window(
        self, readings: Sequence[Any] | np.ndarray, device_id: str
    ) -> DetectionEvent | None:
        """Evaluates an IMU window, checks thresholds and cooldown, and returns a DetectionEvent if a fall occurred."""
        prediction = self.predict(readings)
        if prediction is None:
            return None

        predicted_label, confidence = prediction
        is_fall = predicted_label.startswith("fall_") or predicted_label in (
            "fall",
            "fall_forward",
            "fall_backward",
            "fall_lateral",
        )

        if not is_fall:
            return None

        if confidence < self.confidence_threshold:
            LOGGER.debug(
                "fall_suppressed_low_confidence label=%s confidence=%.2f threshold=%.2f",
                predicted_label,
                confidence,
                self.confidence_threshold,
            )
            return None

        now = self._time_fn()
        with self._lock:
            elapsed = now - self._last_detection_time
            if elapsed < self.cooldown_sec:
                LOGGER.debug(
                    "fall_suppressed_cooldown label=%s elapsed=%.2fs cooldown=%.2fs",
                    predicted_label,
                    elapsed,
                    self.cooldown_sec,
                )
                return None
            self._last_detection_time = now
            model_name = self.model_name
            window_size = self.window_size_sec

        event = DetectionEvent(
            device_id=device_id,
            event_type=predicted_label,
            confidence=round(confidence, 4),
            timestamp=int(datetime.now(timezone.utc).timestamp() * 1000),
            metadata=DetectionMetadata(
                model_name=model_name,
                window_size_sec=window_size,
            ),
        )

        LOGGER.warning(
            "fall_detected device_id=%s type=%s confidence=%.3f",
            device_id,
            predicted_label,
            confidence,
        )
        return event
