import struct
import zlib
import pytest

from healthkicks_edge.transport.ble.burst_packetizer import (
    decode_imu_frame,
    depacketize_burst,
    encode_imu_frame,
    packetize_readings,
)
from healthkicks_edge.transport.ble.constants import (
    ACCEL_SCALE_FACTOR,
    BYTES_PER_IMU_FRAME,
    GYRO_SCALE_FACTOR,
    PACKET_TYPE_DATA_CHUNK,
    PACKET_TYPE_END_OF_BURST,
    PACKET_TYPE_START_OF_BURST,
)


def test_encode_decode_imu_frame_roundtrip():
    delta_ms = 1250
    ax, ay, az = 0.052, 0.981, -0.124
    gx, gy, gz = 12.3, -45.6, 7.8

    raw = encode_imu_frame(delta_ms, ax, ay, az, gx, gy, gz)
    assert len(raw) == BYTES_PER_IMU_FRAME

    decoded = decode_imu_frame(raw)
    assert decoded["delta_ms"] == 1250.0
    assert pytest.approx(decoded["ax"], abs=0.001) == ax
    assert pytest.approx(decoded["ay"], abs=0.001) == ay
    assert pytest.approx(decoded["az"], abs=0.001) == az
    assert pytest.approx(decoded["gx"], abs=0.1) == gx
    assert pytest.approx(decoded["gy"], abs=0.1) == gy
    assert pytest.approx(decoded["gz"], abs=0.1) == gz


def test_encode_imu_frame_clamping():
    # Extreme values should be clamped to int16 range without overflow
    raw = encode_imu_frame(70000, 100.0, -100.0, 0.0, 5000.0, -5000.0, 0.0)
    assert len(raw) == BYTES_PER_IMU_FRAME
    decoded = decode_imu_frame(raw)
    assert decoded["delta_ms"] == 65535.0  # uint16 clamped
    assert decoded["ax"] == 32767 / ACCEL_SCALE_FACTOR
    assert decoded["ay"] == -32768 / ACCEL_SCALE_FACTOR


def test_packetize_empty_readings():
    packets = packetize_readings([])
    assert len(packets) == 2
    # START packet
    assert packets[0][0] == PACKET_TYPE_START_OF_BURST
    # END packet
    assert packets[1][0] == PACKET_TYPE_END_OF_BURST

    frames, is_valid, total = depacketize_burst(packets)
    assert frames == []
    assert is_valid is True
    assert total == 0


def test_packetize_burst_roundtrip_default_mtu():
    sample_readings = [
        {"timestamp": 100.0 + i * 0.02, "ax": 0.1 * i, "ay": 1.0, "az": 0.0, "gx": 0.0, "gy": 0.0, "gz": 0.0}
        for i in range(10)
    ]

    # Standard ATT MTU = 23 (1 frame per chunk)
    packets = packetize_readings(sample_readings, att_mtu=23)
    assert len(packets) == 1 + 10 + 1  # 1 START + 10 CHUNKS + 1 END

    frames, is_valid, total = depacketize_burst(packets)
    assert len(frames) == 10
    assert total == 10
    assert is_valid is True
    assert pytest.approx(frames[0]["ax"], abs=0.001) == 0.0
    assert pytest.approx(frames[9]["ax"], abs=0.001) == 0.9


def test_packetize_burst_roundtrip_extended_mtu():
    # 250 frames (5 seconds @ 50 Hz)
    sample_readings = [
        {"timestamp": 1000.0 + i * 0.02, "ax": 0.01 * (i % 10), "ay": 0.98, "az": 0.12, "gx": 1.0, "gy": 2.0, "gz": 3.0}
        for i in range(250)
    ]

    packets = packetize_readings(sample_readings, att_mtu=247)
    # Expected chunks: 250 frames / 17 frames per packet = 15 chunks
    # Total packets = 1 START + 15 CHUNKS + 1 END = 17 packets
    assert len(packets) == 17

    frames, is_valid, total = depacketize_burst(packets)
    assert len(frames) == 250
    assert total == 250
    assert is_valid is True


def test_depacketize_crc_failure_on_corrupted_data():
    sample_readings = [
        {"timestamp": 100.0, "ax": 0.5, "ay": 0.5, "az": 0.5, "gx": 0.0, "gy": 0.0, "gz": 0.0}
    ]
    packets = packetize_readings(sample_readings, att_mtu=247)

    # Corrupt one payload byte in the DATA_CHUNK packet
    chunk = bytearray(packets[1])
    chunk[-1] ^= 0xFF
    packets[1] = bytes(chunk)

    frames, is_valid, total = depacketize_burst(packets)
    assert is_valid is False
