from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Callable

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest

from schemas import FallEvent, FallPayload, Header, ImuPayload, Telemetry

LOGGER = logging.getLogger(__name__)
AXES = ("ax", "ay", "az", "gx", "gy", "gz")


class EdgeAI:
    def __init__(
        self,
        device_id: str,
        model_path: str,
        window_size: int,
        fall_cooldown: float,
        on_telemetry: Callable[[Telemetry], None],
        on_fall: Callable[[FallEvent], None],
        on_emergency_haptic: Callable[[], None],
    ) -> None:
        self._device_id = device_id
        self._model_path = model_path
        self._window = deque(maxlen=window_size)
        self._cooldown = fall_cooldown
        self._on_telemetry = on_telemetry
        self._on_fall = on_fall
        self._on_emergency_haptic = on_emergency_haptic
        self._model: IsolationForest | None = self._load_model()
        self._last_fall = 0.0
        self._lock = threading.Lock()

    def process(self, values: dict[str, float]) -> None:
        now = datetime.now(timezone.utc)
        telemetry = Telemetry(
            header=Header(device_id=self._device_id, timestamp=now),
            payload=ImuPayload(**values),
        )
        self._on_telemetry(telemetry)
        with self._lock:
            self._window.append([values[axis] for axis in AXES])
            if self._model is None:
                if len(self._window) < self._window.maxlen:
                    return
                self._model = IsolationForest(random_state=42, contamination="auto")
                self._model.fit(np.asarray(self._window))
                self._save_model()
                return
            sample = np.asarray([[values[axis] for axis in AXES]])
            prediction = int(self._model.predict(sample)[0])
            score = float(self._model.decision_function(sample)[0])

        if prediction == -1 and time.monotonic() - self._last_fall >= self._cooldown:
            self._last_fall = time.monotonic()
            event = FallEvent(
                header=Header(device_id=self._device_id, timestamp=now),
                payload=FallPayload(**values, anomaly_score=score),
            )
            self._on_emergency_haptic()
            self._on_fall(event)
            LOGGER.warning("fall_detected device_id=%s score=%.5f", self._device_id, score)

    def _load_model(self) -> IsolationForest | None:
        if not os.path.exists(self._model_path):
            return None
        try:
            model = joblib.load(self._model_path)
            if isinstance(model, IsolationForest):
                LOGGER.info("ai_model_loaded path=%s", self._model_path)
                return model
        except (OSError, ValueError, EOFError) as error:
            LOGGER.warning("ai_model_load_failed error=%s", error)
        return None

    def _save_model(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._model_path), exist_ok=True)
            joblib.dump(self._model, self._model_path)
            LOGGER.info("ai_model_saved path=%s", self._model_path)
        except OSError as error:
            LOGGER.warning("ai_model_save_failed error=%s", error)
