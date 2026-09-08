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
        print(f"  [HAPTIC VIBRATION] -> Intensity: {intensity}/255, Duration: {duration_ms}ms")


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
        description="HealthKicks Studio Mode - Local timestamped IMU capture test",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--label", type=str, default="walk", help="Activity label (e.g. walk, run, stairs)")
    parser.add_argument("--duration", type=float, default=5.0, help="Capture duration in seconds (1.0 to 30.0)")
    parser.add_argument("--session-id", type=str, default=None, help="Unique UUID session identifier")
    parser.add_argument("--pulse-count", type=int, default=3, help="Number of countdown vibration pulses")
    parser.add_argument("--pulse-duration-ms", type=int, default=150, help="Pulse ON duration in ms")
    parser.add_argument("--pulse-pause-ms", type=int, default=350, help="Pulse OFF interval in ms")
    parser.add_argument("--pulse-intensity", type=int, default=180, help="Pulse intensity (50 to 255)")
    parser.add_argument("--device-id", type=str, default=None, help="Device identifier (defaults from Settings)")
    parser.add_argument("--serial-device", type=str, default=None, help="Serial device path (e.g. /dev/ttyUSB0 or COM3)")
    parser.add_argument("--baudrate", type=int, default=None, help="Serial baud rate (default: 115200)")
    parser.add_argument("--simulate", action="store_true", help="Force synthetic IMU generator without opening serial port")

    args = parser.parse_args()

    settings = Settings.from_env()
    device_id = args.device_id or settings.device_id
    serial_device = args.serial_device or settings.serial_device
    baudrate = args.baudrate or settings.serial_baudrate
    session_id = args.session_id or f"studio-{uuid4()}"

    # Configuration validation via Pydantic
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
        print(f"Studio configuration validation error: {exc}", file=sys.stderr)
        return 1

    print("=" * 65)
    print("HEALTHKICKS STUDIO - LOCAL CAPTURE TEST")
    print("=" * 65)
    print(f"  Device ID        : {device_id}")
    print(f"  Session ID       : {config.session_id}")
    print(f"  Label            : {config.label}")
    print(f"  Duration         : {config.duration_sec:.1f} s")
    print(f"  Countdown        : {config.pulse_count}x ({config.pulse_duration_ms}ms ON / {config.pulse_pause_ms}ms OFF, intensity {config.pulse_intensity}/255)")
    print("=" * 65)

    stop_event = threading.Event()
    captured_batches: list[TelemetryBatch] = []

    telemetry_buffer = TelemetryBuffer(
        device_id=device_id,
        max_size=settings.buffer_max_size,
        flush_interval=settings.buffer_flush_interval_seconds,
        publish=captured_batches.append,
        continuously_send_telemetry=settings.continuously_send_telemetry,
        settings=settings,
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
            print(f"Serial connection established on {serial_device} ({baudrate} baud).")
        except Exception as err:
            print(f"[INFO] Unable to open serial port ({err}). Automatically falling back to simulated stream.")
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
        print("Simulated IMU stream started (50 Hz).")

    studio_manager = StudioManager(
        serial_handler=serial_handler,
        telemetry_buffer=telemetry_buffer,
        stop_event=stop_event,
    )

    print("\nStarting Studio sequence...")
    started = studio_manager.start_capture(config)
    if not started:
        print("Error: Unable to start Studio capture (session already in progress).", file=sys.stderr)
        stop_event.set()
        return 1

    # Wait for orchestrator thread to complete
    studio_manager.wait_completion()
    stop_event.set()

    if serial_thread is not None:
        serial_thread.join(timeout=1.0)
    if simulated_thread is not None:
        simulated_thread.join(timeout=1.0)

    # Validate and display captured batch
    studio_batches = [b for b in captured_batches if b.metadata.flush_trigger == "studio"]
    if not studio_batches:
        print("\nNo batch with 'studio' trigger was produced.", file=sys.stderr)
        return 1

    batch = studio_batches[-1]
    print("\n" + "=" * 65)
    print("STUDIO SESSION COMPLETED SUCCESSFULLY")
    print("=" * 65)
    print(f"Captured IMU readings count : {len(batch.readings)}")
    print(f"Trigger                     : {batch.metadata.flush_trigger}")
    print(f"Session ID                  : {batch.metadata.session_id}")
    print(f"Label                       : {batch.metadata.label}")
    print(f"Window start                : {batch.metadata.window_start.isoformat()}")
    print(f"Window end                  : {batch.metadata.window_end.isoformat()}")
    print("-" * 65)
    print("BATCH JSON PAYLOAD (ready for AWS Lambda / DynamoDB):")
    print(json.dumps(json.loads(batch.model_dump_json()), indent=2))
    print("=" * 65)

    return 0


if __name__ == "__main__":
    sys.exit(main())
