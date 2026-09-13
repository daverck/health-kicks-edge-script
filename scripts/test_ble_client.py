"""CLI BLE Test Client for HealthKicks Footwear GATT Server.
Validates:
1. Activity Detection notifications & state parsing
2. Haptic Command binary writes
3. Studio Control session sequence (START -> COUNTDOWN -> RECORDING -> FINISHED)
4. Studio Data Burst packet reception, reassembly, and CRC32 verification.

Contract Reference: contracts/ble_gatt_specs.md
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path
import struct
import sys
import uuid
from typing import Any

# Ensure src/ is on sys.path when run directly
src_dir = str(Path(__file__).resolve().parents[1] / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from healthkicks_edge.transport.ble.burst_packetizer import depacketize_burst, packetize_readings
from healthkicks_edge.transport.ble.constants import (
    CHAR_ACTIVITY_DETECTION_UUID,
    CHAR_HAPTIC_COMMAND_UUID,
    CHAR_STUDIO_CONTROL_UUID,
    CHAR_STUDIO_DATA_BURST_UUID,
    FOOTWEAR_SERVICE_UUID,
    STATE_CODE_TO_EVENT_TYPE,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
LOGGER = logging.getLogger("ble_client")

try:
    from bleak import BleakClient, BleakScanner
    HAS_BLEAK = True
except ImportError:
    HAS_BLEAK = False


def parse_activity_detection(data: bytes) -> dict[str, Any]:
    """Parse 7-byte Activity Detection payload."""
    if len(data) < 7:
        return {"raw": data.hex()}
    state_code, confidence, timestamp, flags = struct.unpack(">BBI B", data[:7])
    event_type = STATE_CODE_TO_EVENT_TYPE.get(state_code, f"unknown_0x{state_code:02x}")
    return {
        "event_type": event_type,
        "confidence_pct": confidence,
        "timestamp_epoch": timestamp,
        "is_fall": bool(flags & 0x01),
        "haptic_triggered": bool(flags & 0x02),
    }


def simulate_offline_burst() -> None:
    """Offline validation of packetizer, MTU chunking, and CRC32 reassembly."""
    LOGGER.info("=== Running Offline BLE Burst Simulation ===")
    sample_readings = []
    base_ts = 1726224000.0
    for i in range(250):  # 5 seconds at 50 Hz
        sample_readings.append({
            "timestamp": base_ts + (i * 0.02),
            "ax": 0.05 + 0.01 * (i % 10),
            "ay": 0.98,
            "az": 0.12,
            "gx": 0.02,
            "gy": -0.01,
            "gz": 0.04,
        })

    LOGGER.info("Generated %d synthetic 50 Hz IMU readings", len(sample_readings))
    packets = packetize_readings(sample_readings, att_mtu=247)
    LOGGER.info("Packetized into %d BLE packets (MTU=247)", len(packets))

    # Reassemble
    recovered, is_valid, total_announced = depacketize_burst(packets)
    LOGGER.info(
        "Reassembly result: recovered=%d samples, announced=%d, CRC32_valid=%s",
        len(recovered),
        total_announced,
        is_valid,
    )
    if is_valid and len(recovered) == len(sample_readings):
        LOGGER.info("SUCCESS: Reassembled dataset matches 100% of input frames with valid CRC32!")
    else:
        LOGGER.error("FAILURE: CRC or count mismatch")
        sys.exit(1)


def match_device(device: Any, adv_data: Any) -> bool:
    """Match device by Service UUID or Advertised Local Name."""
    # 1. Vérification UUID de service (insensible à la casse)
    service_match = any(
        FOOTWEAR_SERVICE_UUID.lower() == str(u).lower()
        for u in (getattr(adv_data, "service_uuids", None) or [])
    )
    if service_match:
        return True

    # 2. Vérification sur le nom (local_name de l'adv ou device.name)
    local_name = getattr(adv_data, "local_name", None) or getattr(device, "name", None) or ""
    if "HealthKicks" in local_name:
        return True

    return False


async def run_ble_live_client(
    device_address: str | None = None,
    duration_sec: float = 3.0,
    timeout_sec: float = 12.0,
) -> None:
    """Connect to the live HealthKicks GATT server and exercise all characteristics."""
    if not HAS_BLEAK:
        LOGGER.error("bleak is required for live testing. Install via: pip install bleak")
        sys.exit(1)

    target_device = None
    discovered_devices: dict[str, tuple[Any, Any]] = {}

    if not device_address:
        LOGGER.info(
            "Scanning for HealthKicks Footwear device (Service: %s, timeout: %.1fs)...",
            FOOTWEAR_SERVICE_UUID,
            timeout_sec,
        )

        def detection_callback(device: Any, adv_data: Any) -> None:
            discovered_devices[device.address] = (device, adv_data)

        try:
            scanner = BleakScanner(detection_callback=detection_callback)
            await scanner.start()
            try:
                start_time = asyncio.get_running_loop().time()
                while (asyncio.get_running_loop().time() - start_time) < timeout_sec:
                    for dev, adv in list(discovered_devices.values()):
                        if match_device(dev, adv):
                            target_device = dev
                            LOGGER.info(
                                "Found matching HealthKicks device: %s (Name: '%s', RSSI: %s dBm)",
                                dev.address,
                                getattr(adv, "local_name", None) or getattr(dev, "name", None) or "<unknown>",
                                getattr(adv, "rssi", "N/A"),
                            )
                            break
                    if target_device:
                        break
                    await asyncio.sleep(0.25)
            finally:
                await scanner.stop()
        except Exception as scan_err:
            LOGGER.warning("Callback scan failed (%s), falling back to BleakScanner.discover()...", scan_err)
            results = await BleakScanner.discover(timeout=timeout_sec, return_adv=True)
            if isinstance(results, dict):
                discovered_devices = results
            elif isinstance(results, list):
                discovered_devices = {d.address: (d, getattr(d, "details", None)) for d in results}
            for dev, adv in discovered_devices.values():
                if adv and match_device(dev, adv):
                    target_device = dev
                    break

        if not target_device:
            LOGGER.error("No HealthKicks device found during scan (timeout: %.1fs).", timeout_sec)
            if discovered_devices:
                LOGGER.info("Nearby BLE devices detected (%d):", len(discovered_devices))
                for addr, item in discovered_devices.items():
                    if isinstance(item, tuple) and len(item) == 2:
                        dev, adv = item
                        name = getattr(adv, "local_name", None) or getattr(dev, "name", None) or "<Unknown>"
                        rssi = getattr(adv, "rssi", None) or getattr(dev, "rssi", "N/A")
                        uuids = getattr(adv, "service_uuids", None) or []
                    else:
                        dev = item
                        name = getattr(dev, "name", "<Unknown>")
                        rssi = getattr(dev, "rssi", "N/A")
                        uuids = []
                    LOGGER.info("  - %s | Name: %s | RSSI: %s dBm | Services: %s", addr, name, rssi, uuids)
            else:
                LOGGER.warning("No BLE devices detected nearby. Check that Bluetooth is enabled on this host.")
            return

        device_address = target_device.address

    LOGGER.info("Connecting to GATT server at %s...", device_address)
    burst_packets: list[bytes] = []
    burst_finished_event = asyncio.Event()

    def on_activity_notify(_: Any, data: bytearray) -> None:
        parsed = parse_activity_detection(bytes(data))
        LOGGER.info("[NOTIFY] Activity Detection: %s", parsed)

    def on_studio_control_notify(_: Any, data: bytearray) -> None:
        msg = bytes(data).decode("utf-8", errors="replace")
        LOGGER.info("[NOTIFY] Studio Control: %s", msg)
        if msg.startswith("FINISHED"):
            LOGGER.info("Studio capture completed on device. Waiting for data burst packets...")

    def on_burst_notify(_: Any, data: bytearray) -> None:
        pkt = bytes(data)
        burst_packets.append(pkt)
        pkt_type = pkt[0] if pkt else 0
        if pkt_type == 0x03:  # END_OF_BURST
            LOGGER.info("Received END_OF_BURST packet (total packets: %d)", len(burst_packets))
            burst_finished_event.set()

    async with BleakClient(target_device or device_address) as client:
        LOGGER.info("Connected to %s (MTU: %d)", device_address, client.mtu_size)

        # 1. Subscribe to notifications
        await client.start_notify(CHAR_ACTIVITY_DETECTION_UUID, on_activity_notify)
        await client.start_notify(CHAR_STUDIO_CONTROL_UUID, on_studio_control_notify)
        await client.start_notify(CHAR_STUDIO_DATA_BURST_UUID, on_burst_notify)
        LOGGER.info("Subscribed to all notification characteristics.")

        # 2. Test Haptic Command write
        LOGGER.info("Writing test Haptic Command (intensity=200, duration=400ms)...")
        haptic_payload = struct.pack(">BBH", 0x00, 200, 400)
        await client.write_gatt_char(CHAR_HAPTIC_COMMAND_UUID, haptic_payload, response=True)
        LOGGER.info("Haptic command dispatched successfully.")

        # 3. Test Studio Session trigger
        sess_id = str(uuid.uuid4())
        cmd_text = f"START test_session {duration_sec:.1f} {sess_id}"
        LOGGER.info("Triggering Studio session via Studio Control: '%s'", cmd_text)
        await client.write_gatt_char(CHAR_STUDIO_CONTROL_UUID, cmd_text.encode("utf-8"), response=True)

        # 4. Wait for burst transfer completion
        try:
            await asyncio.wait_for(burst_finished_event.wait(), timeout=duration_sec + 10.0)
            LOGGER.info("Burst transfer completed. Reassembling %d packets...", len(burst_packets))
            frames, is_valid, total = depacketize_burst(burst_packets)
            LOGGER.info(
                "Burst results: frames=%d, announced=%d, CRC32_valid=%s",
                len(frames),
                total,
                is_valid,
            )
            if frames:
                LOGGER.info("Sample frame [0]: %s", frames[0])
                LOGGER.info("Sample frame [-1]: %s", frames[-1])
        except asyncio.TimeoutError:
            LOGGER.warning("Timeout waiting for burst transfer completion.")

        # Cleanup
        await client.stop_notify(CHAR_ACTIVITY_DETECTION_UUID)
        await client.stop_notify(CHAR_STUDIO_CONTROL_UUID)
        await client.stop_notify(CHAR_STUDIO_DATA_BURST_UUID)
        LOGGER.info("Test complete.")


def main() -> None:
    parser = argparse.ArgumentParser(description="HealthKicks BLE GATT Client Test Tool")
    parser.add_argument(
        "-a", "--address",
        help="Bluetooth device MAC / UUID address (bypasses active scan if specified)",
    )
    parser.add_argument(
        "-t", "--timeout",
        type=float,
        default=12.0,
        help="BLE scan timeout in seconds when searching for device (default: 12.0)",
    )
    parser.add_argument(
        "-d", "--duration",
        type=float,
        default=3.0,
        help="Studio capture duration in seconds (default: 3.0)",
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Run offline burst packetization simulation",
    )
    args = parser.parse_args()

    if args.simulate or not HAS_BLEAK:
        simulate_offline_burst()
    else:
        asyncio.run(
            run_ble_live_client(
                device_address=args.address,
                duration_sec=args.duration,
                timeout_sec=args.timeout,
            )
        )


if __name__ == "__main__":
    main()
