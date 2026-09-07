from __future__ import annotations

import argparse
import json
import logging
import math
import sys
import threading
import time
from datetime import datetime, timezone
from uuid import uuid4

from config import Settings
from schemas import Header, ImuPayload, StudioCaptureConfig, Telemetry, TelemetryBatch
from serial_handler import SerialHandler
from studio_manager import StudioManager
from telemetry_buffer import TelemetryBuffer


class MockSerialHandler:
    """Mock serial handler used when hardware serial device is not accessible."""

    def __init__(self) -> None:
        self.enqueued_pulses: list[tuple[int, int]] = []

    def enqueue_haptic(self, intensity: int, duration_ms: int) -> None:
        self.enqueued_pulses.append((intensity, duration_ms))
        print(f"  [HAPTIC VIBRATION] -> Intensité: {intensity}/255, Durée: {duration_ms}ms")


def _run_simulated_imu_stream(
    stop_event: threading.Event,
    device_id: str,
    on_telemetry_cb,
    rate_hz: float = 50.0,
) -> None:
    """Simulate realistic IMU readings at rate_hz until stop_event is set."""
    dt = 1.0 / rate_hz
    step = 0
    while not stop_event.is_set():
        t = step * dt
        # Synthetic walking pattern
        ax = 0.1 * math.sin(2 * math.pi * 1.5 * t)
        ay = 0.15 * math.cos(2 * math.pi * 1.5 * t)
        az = 9.81 + 1.2 * math.sin(2 * math.pi * 3.0 * t)
        gx = 0.05 * math.sin(2 * math.pi * 1.5 * t)
        gy = 0.08 * math.cos(2 * math.pi * 1.5 * t)
        gz = 0.02 * math.sin(2 * math.pi * 3.0 * t)

        telemetry = Telemetry(
            header=Header(device_id=device_id, timestamp=datetime.now(timezone.utc)),
            payload=ImuPayload(ax=ax, ay=ay, az=az, gx=gx, gy=gy, gz=gz),
        )
        on_telemetry_cb(telemetry)
        step += 1
        time.sleep(dt)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="HealthKicks Studio Mode - Test local de capture IMU horodatée",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--label", type=str, default="walk", help="Libellé d'activité (ex: walk, run, stairs)")
    parser.add_argument("--duration", type=float, default=5.0, help="Durée de capture en secondes (1.0 à 30.0)")
    parser.add_argument("--session-id", type=str, default=None, help="Identifiant UUID unique de session")
    parser.add_argument("--pulse-count", type=int, default=3, help="Nombre d'impulsions de compte à rebours")
    parser.add_argument("--pulse-duration-ms", type=int, default=150, help="Durée d'une vibration en ms")
    parser.add_argument("--pulse-pause-ms", type=int, default=350, help="Pause entre vibrations en ms")
    parser.add_argument("--pulse-intensity", type=int, default=180, help="Intensité des vibrations (50 à 255)")
    parser.add_argument("--device-id", type=str, default=None, help="Identifiant du device (défaut depuis Settings)")
    parser.add_argument("--serial-device", type=str, default=None, help="Port série (ex: /dev/ttyUSB0 ou COM3)")
    parser.add_argument("--baudrate", type=int, default=None, help="Vitesse série en bauds (défaut: 115200)")
    parser.add_argument("--simulate", action="store_true", help="Forcer le générateur IMU simulé sans ouvrir le port série")

    args = parser.parse_args()

    settings = Settings.from_env()
    device_id = args.device_id or settings.device_id
    serial_device = args.serial_device or settings.serial_device
    baudrate = args.baudrate or settings.serial_baudrate
    session_id = args.session_id or f"studio-{uuid4()}"

    # Validation de la configuration via Pydantic
    try:
        config = StudioCaptureConfig(
            session_id=session_id,
            label=args.label,
            duration_sec=args.duration,
            pulse_count=args.pulse_count,
            pulse_duration_ms=args.pulse_duration_ms,
            pulse_pause_ms=args.pulse_pause_ms,
            pulse_intensity=args.pulse_intensity,
        )
    except Exception as exc:
        print(f"Erreur de validation de la configuration Studio : {exc}", file=sys.stderr)
        return 1

    print("=" * 65)
    print("HEALTHKICKS STUDIO - TEST DE CAPTURE LOCAL")
    print("=" * 65)
    print(f"  Device ID        : {device_id}")
    print(f"  Session ID       : {config.session_id}")
    print(f"  Label            : {config.label}")
    print(f"  Durée de capture : {config.duration_sec:.1f} s")
    print(f"  Compte à rebours : {config.pulse_count}x ({config.pulse_duration_ms}ms ON / {config.pulse_pause_ms}ms OFF, intensité {config.pulse_intensity}/255)")
    print("=" * 65)

    stop_event = threading.Event()
    captured_batches: list[TelemetryBatch] = []

    telemetry_buffer = TelemetryBuffer(
        device_id=device_id,
        max_size=settings.buffer_max_size,
        flush_interval=settings.buffer_flush_interval_seconds,
        publish=captured_batches.append,
    )

    serial_handler: Any = None
    serial_thread: threading.Thread | None = None
    simulated_thread: threading.Thread | None = None

    if not args.simulate:
        try:
            import serial
            test_ser = serial.Serial(serial_device, baudrate, timeout=0.1)
            test_ser.close()

            def on_raw_serial_data(values: dict[str, float]) -> None:
                telemetry = Telemetry(
                    header=Header(device_id=device_id, timestamp=datetime.now(timezone.utc)),
                    payload=ImuPayload(**values),
                )
                telemetry_buffer.append(telemetry)

            serial_handler = SerialHandler(
                device=serial_device,
                baudrate=baudrate,
                stop_event=stop_event,
                command_ttl=settings.command_ttl_seconds,
                on_data=on_raw_serial_data,
            )
            serial_thread = threading.Thread(target=serial_handler.run, name="serial-reader", daemon=True)
            serial_thread.start()
            print(f"Connexion série établie sur {serial_device} ({baudrate} bauds).")
        except Exception as err:
            print(f"[INFO] Impossible d'ouvrir le port série ({err}). Bascule automatique sur flux simulé.")
            serial_handler = None

    if serial_handler is None:
        serial_handler = MockSerialHandler()
        simulated_thread = threading.Thread(
            target=_run_simulated_imu_stream,
            args=(stop_event, device_id, telemetry_buffer.append),
            name="simulated-imu",
            daemon=True,
        )
        simulated_thread.start()
        print("Flux IMU simulé démarré (50 Hz).")

    studio_manager = StudioManager(
        serial_handler=serial_handler,
        telemetry_buffer=telemetry_buffer,
        stop_event=stop_event,
    )

    print("\nLancement de la séquence Studio...")
    started = studio_manager.start_capture(config)
    if not started:
        print("Erreur : Impossible de démarrer la capture studio (session déjà en cours).", file=sys.stderr)
        stop_event.set()
        return 1

    # Attente de la fin de l'orchestrateur
    studio_manager.wait_completion()
    stop_event.set()

    if serial_thread is not None:
        serial_thread.join(timeout=1.0)
    if simulated_thread is not None:
        simulated_thread.join(timeout=1.0)

    # Vérification et affichage du lot capturé
    studio_batches = [b for b in captured_batches if b.metadata.flush_trigger == "studio"]
    if not studio_batches:
        print("\nAucun lot avec le déclencheur 'studio' n'a été produit.", file=sys.stderr)
        return 1

    batch = studio_batches[-1]
    print("\n" + "=" * 65)
    print("SESSION STUDIO TERMINÉE AVEC SUCCÈS")
    print("=" * 65)
    print(f"Nombre de points IMU capturés : {len(batch.readings)}")
    print(f"Trigger                       : {batch.metadata.flush_trigger}")
    print(f"Session ID                    : {batch.metadata.session_id}")
    print(f"Label                         : {batch.metadata.label}")
    print(f"Début de fenêtre              : {batch.metadata.window_start.isoformat()}")
    print(f"Fin de fenêtre                : {batch.metadata.window_end.isoformat()}")
    print("-" * 65)
    print("PAYLOAD JSON DU BATCH (prêt pour AWS Lambda / DynamoDB) :")
    print(json.dumps(json.loads(batch.model_dump_json()), indent=2))
    print("=" * 65)

    return 0


if __name__ == "__main__":
    sys.exit(main())
