from __future__ import annotations

import logging
import signal
import threading

from ai_engine import EdgeAI
from config import Settings
from mqtt_handler import MQTTHandler
from serial_handler import SerialHandler
from telemetry_buffer import TelemetryBuffer


def main() -> None:
    settings = Settings.from_env()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    stop_event = threading.Event()

    mqtt_handler: MQTTHandler
    serial_handler: SerialHandler
    telemetry_buffer: TelemetryBuffer

    def on_telemetry(telemetry) -> None:
        trigger = telemetry_buffer.append(telemetry)
        if trigger:
            count = telemetry_buffer.flush(trigger)
            logging.getLogger(__name__).debug("telemetry_flushed samples=%d trigger=%s", count, trigger)

    def on_fall(event) -> None:
        mqtt_handler.publish_fall(event)

    def emergency_haptic() -> None:
        serial_handler.enqueue_haptic(255, 500)

    telemetry_buffer = TelemetryBuffer(
        device_id=settings.device_id,
        max_size=settings.buffer_max_size,
        flush_interval=settings.buffer_flush_interval_seconds,
        publish=lambda batch: mqtt_handler.publish_batch(batch),
    )
    ai_engine = EdgeAI(
        device_id=settings.device_id,
        model_path=settings.model_path,
        window_size=settings.model_window_size,
        fall_cooldown=settings.fall_cooldown_seconds,
        on_telemetry=on_telemetry,
        on_fall=on_fall,
        on_emergency_haptic=emergency_haptic,
    )
    mqtt_handler = MQTTHandler(
        host=settings.mqtt_host,
        port=settings.mqtt_port,
        client_id=settings.mqtt_client_id,
        username=settings.mqtt_username,
        password=settings.mqtt_password,
        device_id=settings.device_id,
        telemetry_topic=settings.telemetry_topic,
        fall_topic=settings.fall_topic,
        command_topic=settings.command_topic,
        status_topic=settings.status_topic,
        ack_topic=settings.ack_topic,
        heartbeat_interval=settings.heartbeat_interval_seconds,
        on_haptic_command=lambda command: serial_handler.enqueue_haptic(
            command.intensity, command.duration_ms
        ),
    )
    serial_handler = SerialHandler(
        device=settings.serial_device,
        baudrate=settings.serial_baudrate,
        stop_event=stop_event,
        command_ttl=settings.command_ttl_seconds,
        on_data=ai_engine.process,
        on_response=mqtt_handler.publish_arduino_response,
    )

    def request_shutdown(signum: int, _: object) -> None:
        logging.getLogger(__name__).info("shutdown_signal signal=%s", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)
    serial_thread = threading.Thread(target=serial_handler.run, name="serial-reader", daemon=True)
    heartbeat_thread = threading.Thread(target=mqtt_handler.heartbeat_loop, name="heartbeat", daemon=True)
    serial_thread.start()
    mqtt_handler.start()
    heartbeat_thread.start()

    try:
        while not stop_event.wait(1.0):
            if telemetry_buffer.due():
                telemetry_buffer.flush("time_interval")
    finally:
        telemetry_buffer.flush("shutdown")
        mqtt_handler.stop()
        serial_thread.join(timeout=3.0)
        heartbeat_thread.join(timeout=3.0)


if __name__ == "__main__":
    main()
