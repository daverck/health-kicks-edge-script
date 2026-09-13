"""Binary packetizer and depacketizer for HealthKicks Studio Data Burst transfer over BLE.
Contract Reference: contracts/ble_gatt_specs.md (Characteristic 4: Studio Data Burst)
"""
from __future__ import annotations

import struct
import zlib
from typing import Any, Sequence

from healthkicks_edge.transport.ble.constants import (
    ACCEL_SCALE_FACTOR,
    BYTES_PER_IMU_FRAME,
    GYRO_SCALE_FACTOR,
    PACKET_HEADER_SIZE,
    PACKET_TYPE_DATA_CHUNK,
    PACKET_TYPE_END_OF_BURST,
    PACKET_TYPE_START_OF_BURST,
    RECOMMENDED_ATT_MTU,
)


def encode_imu_frame(
    delta_ms: int,
    ax: float,
    ay: float,
    az: float,
    gx: float,
    gy: float,
    gz: float,
) -> bytes:
    """Pack an individual IMU sensor reading into 14 big-endian bytes.

    Format:
        delta_ms : uint16 (0..65535 ms)
        ax, ay, az : 3 x int16 (milli-g, scaled by 1000)
        gx, gy, gz : 3 x int16 (tenths of deg/s, scaled by 10)
    """
    # Clamp to int16 range [-32768, 32767]
    ax_scaled = max(-32768, min(32767, int(round(ax * ACCEL_SCALE_FACTOR))))
    ay_scaled = max(-32768, min(32767, int(round(ay * ACCEL_SCALE_FACTOR))))
    az_scaled = max(-32768, min(32767, int(round(az * ACCEL_SCALE_FACTOR))))
    gx_scaled = max(-32768, min(32767, int(round(gx * GYRO_SCALE_FACTOR))))
    gy_scaled = max(-32768, min(32767, int(round(gy * GYRO_SCALE_FACTOR))))
    gz_scaled = max(-32768, min(32767, int(round(gz * GYRO_SCALE_FACTOR))))
    delta_uint16 = max(0, min(65535, delta_ms))

    return struct.pack(
        ">Hhhhhhh",
        delta_uint16,
        ax_scaled,
        ay_scaled,
        az_scaled,
        gx_scaled,
        gy_scaled,
        gz_scaled,
    )


def decode_imu_frame(raw_bytes: bytes) -> dict[str, float]:
    """Unpack 14 bytes into a dictionary of physical IMU values."""
    if len(raw_bytes) != BYTES_PER_IMU_FRAME:
        raise ValueError(f"Expected {BYTES_PER_IMU_FRAME} bytes for IMU frame, got {len(raw_bytes)}")

    delta_ms, ax_raw, ay_raw, az_raw, gx_raw, gy_raw, gz_raw = struct.unpack(
        ">Hhhhhhh", raw_bytes
    )

    return {
        "delta_ms": float(delta_ms),
        "ax": ax_raw / ACCEL_SCALE_FACTOR,
        "ay": ay_raw / ACCEL_SCALE_FACTOR,
        "az": az_raw / ACCEL_SCALE_FACTOR,
        "gx": gx_raw / GYRO_SCALE_FACTOR,
        "gy": gy_raw / GYRO_SCALE_FACTOR,
        "gz": gz_raw / GYRO_SCALE_FACTOR,
    }


def packetize_readings(
    readings: Sequence[Any],
    att_mtu: int = RECOMMENDED_ATT_MTU,
    session_start_timestamp: float | None = None,
) -> list[bytes]:
    """Convert a sequence of Telemetry readings into BLE burst packets according to ATT MTU.

    Packet Structure:
        [0]     : packet_type (0x01 = START, 0x02 = CHUNK, 0x03 = END)
        [1..2]  : seq_num (uint16 big-endian)
        [3]     : payload_len / frame_count (uint8)
        [4..]   : frames payload (N * 14 bytes)

    End Packet:
        [0]     : 0x03 (END_OF_BURST)
        [1..2]  : seq_num (uint16 big-endian)
        [3]     : 0x00
        [4..7]  : total_samples (uint32 big-endian)
        [8..11] : crc32 (uint32 big-endian) calculated over all frame payload bytes.
    """
    # Usable payload space per packet (ATT MTU - 3 ATT protocol bytes - 4 packet header bytes)
    max_payload_bytes = max(BYTES_PER_IMU_FRAME, att_mtu - 3 - PACKET_HEADER_SIZE)
    frames_per_packet = max(1, max_payload_bytes // BYTES_PER_IMU_FRAME)

    packets: list[bytes] = []
    seq_num = 0

    # 1. Packet START_OF_BURST
    # Format: [0x01, seq_num (2B), 0x00 (1B), total_samples (4B uint32)]
    total_count = len(readings)
    start_packet = struct.pack(">BHBI", PACKET_TYPE_START_OF_BURST, seq_num, 0, total_count)
    packets.append(start_packet)
    seq_num += 1

    if not readings:
        # Empty burst
        end_packet = struct.pack(">BHBII", PACKET_TYPE_END_OF_BURST, seq_num, 0, 0, 0)
        packets.append(end_packet)
        return packets

    # Determine reference timestamp for delta_ms
    if session_start_timestamp is None:
        first = readings[0]
        if hasattr(first, "header") and hasattr(first.header, "timestamp"):
            session_start_timestamp = first.header.timestamp.timestamp()
        elif isinstance(first, dict) and "timestamp" in first:
            session_start_timestamp = float(first["timestamp"])
        else:
            session_start_timestamp = 0.0

    all_payload_bytes = bytearray()
    current_chunk_frames = bytearray()
    current_count = 0

    for item in readings:
        if hasattr(item, "header") and hasattr(item, "payload"):
            # Telemetry model
            ts = item.header.timestamp.timestamp()
            ax, ay, az = item.payload.ax, item.payload.ay, item.payload.az
            gx, gy, gz = item.payload.gx, item.payload.gy, item.payload.gz
        elif isinstance(item, dict):
            ts = float(item.get("timestamp", session_start_timestamp))
            ax = float(item.get("ax", 0.0))
            ay = float(item.get("ay", 0.0))
            az = float(item.get("az", 0.0))
            gx = float(item.get("gx", 0.0))
            gy = float(item.get("gy", 0.0))
            gz = float(item.get("gz", 0.0))
        else:
            continue

        delta_ms = max(0, int(round((ts - session_start_timestamp) * 1000.0)))
        frame_bytes = encode_imu_frame(delta_ms, ax, ay, az, gx, gy, gz)
        current_chunk_frames.extend(frame_bytes)
        all_payload_bytes.extend(frame_bytes)
        current_count += 1

        if current_count >= frames_per_packet:
            header = struct.pack(">BHB", PACKET_TYPE_DATA_CHUNK, seq_num, current_count)
            packets.append(bytes(header + current_chunk_frames))
            seq_num += 1
            current_chunk_frames.clear()
            current_count = 0

    # Flush remaining frames
    if current_count > 0:
        header = struct.pack(">BHB", PACKET_TYPE_DATA_CHUNK, seq_num, current_count)
        packets.append(bytes(header + current_chunk_frames))
        seq_num += 1

    # Compute CRC32 over all transmitted frame bytes
    checksum = zlib.crc32(all_payload_bytes) & 0xFFFFFFFF

    # 3. Packet END_OF_BURST
    end_packet = struct.pack(
        ">BHBII",
        PACKET_TYPE_END_OF_BURST,
        seq_num,
        0,
        total_count,
        checksum,
    )
    packets.append(end_packet)

    return packets


def depacketize_burst(packets: Sequence[bytes]) -> tuple[list[dict[str, float]], bool, int]:
    """Reassemble BLE burst packets back into IMU frame dictionaries and verify CRC32.

    Returns:
        (readings, is_crc_valid, total_samples_announced)
    """
    collected_frames: list[dict[str, float]] = []
    accumulated_bytes = bytearray()
    announced_total = 0
    announced_crc = 0
    received_end = False

    for pkt in packets:
        if len(pkt) < PACKET_HEADER_SIZE:
            continue

        packet_type, seq_num, payload_len = struct.unpack(">BHB", pkt[:PACKET_HEADER_SIZE])
        payload = pkt[PACKET_HEADER_SIZE:]

        if packet_type == PACKET_TYPE_START_OF_BURST:
            if len(payload) >= 4:
                announced_total = struct.unpack(">I", payload[:4])[0]
        elif packet_type == PACKET_TYPE_DATA_CHUNK:
            accumulated_bytes.extend(payload)
            # Parse individual 14-byte frames
            for offset in range(0, len(payload), BYTES_PER_IMU_FRAME):
                frame_chunk = payload[offset : offset + BYTES_PER_IMU_FRAME]
                if len(frame_chunk) == BYTES_PER_IMU_FRAME:
                    collected_frames.append(decode_imu_frame(frame_chunk))
        elif packet_type == PACKET_TYPE_END_OF_BURST:
            received_end = True
            if len(payload) >= 8:
                end_total, announced_crc = struct.unpack(">II", payload[:8])
                if announced_total == 0:
                    announced_total = end_total

    computed_crc = zlib.crc32(accumulated_bytes) & 0xFFFFFFFF
    is_valid = received_end and (computed_crc == announced_crc)

    return collected_frames, is_valid, announced_total
