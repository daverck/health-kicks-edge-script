"""Bluetooth Low Energy (BLE) GATT Profil & Constants for HealthKicks Footwear.
Contract Reference: contracts/ble_gatt_specs.md
"""
from __future__ import annotations

# Service 128-bit UUIDs
BASE_UUID = "7a5a0000-c529-4d64-8848-18e5904de22a"
FOOTWEAR_SERVICE_UUID = "7a5a0001-c529-4d64-8848-18e5904de22a"

# Characteristic 128-bit UUIDs
CHAR_ACTIVITY_DETECTION_UUID = "7a5a0002-c529-4d64-8848-18e5904de22a"
CHAR_HAPTIC_COMMAND_UUID = "7a5a0003-c529-4d64-8848-18e5904de22a"
CHAR_STUDIO_CONTROL_UUID = "7a5a0004-c529-4d64-8848-18e5904de22a"
CHAR_STUDIO_DATA_BURST_UUID = "7a5a0005-c529-4d64-8848-18e5904de22a"

# Activity Detection State Codes (uint8)
STATE_CODE_IDLE = 0x00
STATE_CODE_WALK = 0x01
STATE_CODE_RUN = 0x02
STATE_CODE_FALL_FORWARD = 0x10
STATE_CODE_FALL_BACKWARD = 0x11
STATE_CODE_FALL_LATERAL = 0x12
STATE_CODE_FALL_GENERIC = 0x1F

EVENT_TYPE_TO_STATE_CODE: dict[str, int] = {
    "idle": STATE_CODE_IDLE,
    "walk": STATE_CODE_WALK,
    "run": STATE_CODE_RUN,
    "fall_forward": STATE_CODE_FALL_FORWARD,
    "fall_backward": STATE_CODE_FALL_BACKWARD,
    "fall_lateral": STATE_CODE_FALL_LATERAL,
    "fall_generic": STATE_CODE_FALL_GENERIC,
    "fall": STATE_CODE_FALL_GENERIC,
}

STATE_CODE_TO_EVENT_TYPE: dict[int, str] = {
    v: k for k, v in EVENT_TYPE_TO_STATE_CODE.items() if k != "fall"
}

# Studio Data Burst Packet Types (uint8)
PACKET_TYPE_START_OF_BURST = 0x01
PACKET_TYPE_DATA_CHUNK = 0x02
PACKET_TYPE_END_OF_BURST = 0x03

# Fixed sizes & scaling factors
BYTES_PER_IMU_FRAME = 14
PACKET_HEADER_SIZE = 4
ACCEL_SCALE_FACTOR = 1000.0  # 1.0 g -> 1000 milli-g
GYRO_SCALE_FACTOR = 10.0     # 1.0 deg/s -> 10 tenths of deg/s

# Standard & Default MTU values
DEFAULT_ATT_MTU = 23
MAX_ATT_MTU = 517
RECOMMENDED_ATT_MTU = 247
