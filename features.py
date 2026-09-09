from __future__ import annotations

from typing import Any, Sequence
import numpy as np

# Canonical 16 biomechanical features in order expected by activity_classifier.joblib
FEATURE_NAMES: list[str] = [
    "acc_mag_max",
    "acc_mag_min",
    "acc_mag_mean",
    "acc_mag_std",
    "acc_mag_peak_to_peak",
    "gyro_mag_max",
    "gyro_mag_mean",
    "gyro_mag_std",
    "ax_std",
    "ay_std",
    "az_std",
    "gx_std",
    "gy_std",
    "gz_std",
    "acc_energy",
    "gyro_energy",
]

AXES: tuple[str, ...] = ("ax", "ay", "az", "gx", "gy", "gz")


def compute_window_features(
    readings: Sequence[Any] | np.ndarray,
) -> dict[str, float]:
    """Computes 16 statistical and biomechanical features from an IMU window.

    Accepts:
    - Sequence of Telemetry objects (with .payload.ax, etc.)
    - Sequence of dicts with keys 'ax', 'ay', 'az', 'gx', 'gy', 'gz'
    - 2D NumPy array of shape (N, 6) with columns [ax, ay, az, gx, gy, gz]
    - Pandas DataFrame with axes columns

    Returns:
        Dictionary mapping all 16 feature names to float values.
    """
    if len(readings) == 0:
        raise ValueError("Cannot compute features on an empty IMU window")

    # Handle pandas DataFrame if passed
    if hasattr(readings, "to_numpy") and hasattr(readings, "columns"):
        data = readings[["ax", "ay", "az", "gx", "gy", "gz"]].to_numpy(dtype=np.float64)
    elif isinstance(readings, np.ndarray):
        if readings.ndim != 2 or readings.shape[1] != 6:
            raise ValueError(
                f"Expected 2D array of shape (N, 6), got shape {readings.shape}"
            )
        data = np.asarray(readings, dtype=np.float64)
    else:
        first = readings[0]
        if hasattr(first, "payload"):
            # Telemetry model object
            data = np.array(
                [
                    [
                        float(r.payload.ax),
                        float(r.payload.ay),
                        float(r.payload.az),
                        float(r.payload.gx),
                        float(r.payload.gy),
                        float(r.payload.gz),
                    ]
                    for r in readings
                ],
                dtype=np.float64,
            )
        elif isinstance(first, dict):
            data = np.array(
                [
                    [
                        float(r["ax"]),
                        float(r["ay"]),
                        float(r["az"]),
                        float(r["gx"]),
                        float(r["gy"]),
                        float(r["gz"]),
                    ]
                    for r in readings
                ],
                dtype=np.float64,
            )
        elif hasattr(first, "__getitem__"):
            data = np.asarray(readings, dtype=np.float64)
        else:
            raise TypeError(f"Unsupported reading type in window: {type(first)}")

    ax = data[:, 0]
    ay = data[:, 1]
    az = data[:, 2]
    gx = data[:, 3]
    gy = data[:, 4]
    gz = data[:, 5]

    # Euclidean magnitudes
    acc_mag = np.sqrt(ax**2 + ay**2 + az**2)
    gyro_mag = np.sqrt(gx**2 + gy**2 + gz**2)

    n_samples = max(len(acc_mag), 1)

    acc_max = float(np.max(acc_mag))
    acc_min = float(np.min(acc_mag))

    features: dict[str, float] = {
        # Acceleration magnitude indicators (free-fall valley + impact peak)
        "acc_mag_max": acc_max,
        "acc_mag_min": acc_min,
        "acc_mag_mean": float(np.mean(acc_mag)),
        "acc_mag_std": float(np.std(acc_mag)),
        "acc_mag_peak_to_peak": acc_max - acc_min,
        # Gyroscope magnitude indicators (rotational dynamics)
        "gyro_mag_max": float(np.max(gyro_mag)),
        "gyro_mag_mean": float(np.mean(gyro_mag)),
        "gyro_mag_std": float(np.std(gyro_mag)),
        # Individual tri-axial standard deviations
        "ax_std": float(np.std(ax)),
        "ay_std": float(np.std(ay)),
        "az_std": float(np.std(az)),
        "gx_std": float(np.std(gx)),
        "gy_std": float(np.std(gy)),
        "gz_std": float(np.std(gz)),
        # Kinetic / dynamic energy approximations
        "acc_energy": float(np.sum(acc_mag**2) / n_samples),
        "gyro_energy": float(np.sum(gyro_mag**2) / n_samples),
    }

    return features


def extract_feature_vector(
    readings: Sequence[Any] | np.ndarray,
    feature_names: Sequence[str] | None = None,
) -> np.ndarray:
    """Extracts features from readings and formats them as a 2D numpy array (1, n_features)."""
    names = list(feature_names) if feature_names is not None else FEATURE_NAMES
    features_dict = compute_window_features(readings)
    row = [features_dict[k] for k in names]
    return np.array([row], dtype=np.float64)
