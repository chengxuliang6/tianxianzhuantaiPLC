"""The software-side Modbus register contract and integer codec.

The order of multi-register values is intentionally centralized here.  Hardware
writes remain disabled until the PLC word-order magic probe is verified.
"""

from __future__ import annotations

from enum import IntEnum
from typing import Sequence


EVENT_RECORD_WORDS = 6
EVENT_RECORD_COUNT = 360
PROTOCOL_VERSION = 2
WORD_ORDER_PROBE = (0x1234, 0x5678)


class Register(IntEnum):
    """0-based Modbus holding-register addresses, equal to PLC D numbers."""

    # D0-D19 command and parameter block
    MODE = 0
    DIRECTION = 1
    SPEED_INDEX = 2
    START_SEQ = 3
    STOP_SEQ = 4
    SET_ZERO_SEQ = 5
    RESET_FAULT_SEQ = 6
    POWER_SEQ = 7
    HEARTBEAT = 8
    BUFFER_ACK_SEQ = 9
    TOTAL_RATIO_HI = 10
    TOTAL_RATIO_LO = 11
    ACCELERATION_HI = 12
    ACCELERATION_LO = 13
    DECELERATION_HI = 14
    DECELERATION_LO = 15
    SOFTWARE_STOP_DECELERATION_HI = 16
    SOFTWARE_STOP_DECELERATION_LO = 17
    BACKLASH_COMPENSATION_HI = 18
    BACKLASH_COMPENSATION_LO = 19

    # D100-D120 status and run metadata block
    RUN_STATE = 100
    STATUS_FLAGS = 101
    FAULT_CODE = 102
    ACTUAL_POSITION_HI = 103
    ACTUAL_POSITION_LO = 104
    TARGET_POSITION_HI = 105
    TARGET_POSITION_LO = 106
    ACTUAL_VELOCITY_HI = 107
    ACTUAL_VELOCITY_LO = 108
    HEARTBEAT_ECHO = 109
    START_ACK_SEQ = 110
    STOP_ACK_SEQ = 111
    SET_ZERO_ACK_SEQ = 112
    RESET_FAULT_ACK_SEQ = 113
    POWER_ACK_SEQ = 114
    BUFFER_ACKED_SEQ = 115
    EVENT_COUNT = 116
    EVENT_GENERATION = 117
    RUN_STATUS = 118
    RUN_START_TICK_MS_HI = 119
    RUN_START_TICK_MS_LO = 120

    # D200-D206 time synchronization and protocol probe block
    PROTOCOL_VERSION = 200
    WORD_ORDER_PROBE_HI = 201
    WORD_ORDER_PROBE_LO = 202
    TIME_SYNC_REQUEST_SEQ = 203
    PLC_TICK_MS_HI = 204
    PLC_TICK_MS_LO = 205
    TIME_SYNC_RESPONSE_SEQ = 206

    # D2000-D4159 event buffer block
    EVENT_BUFFER_BASE = 2000

    @classmethod
    def event_last_address(cls) -> int:
        """Return the final address in the fixed 360-record event buffer."""
        return cls.EVENT_BUFFER_BASE + EVENT_RECORD_WORDS * EVENT_RECORD_COUNT - 1


def encode_i32(value: int) -> tuple[int, int]:
    """Encode a signed 32-bit integer as unsigned Modbus words, high word first."""
    if not -(2**31) <= value <= 2**31 - 1:
        raise ValueError("value must fit in a signed 32-bit integer")
    raw = value & 0xFFFFFFFF
    return (raw >> 16) & 0xFFFF, raw & 0xFFFF


def decode_i32(words: Sequence[object]) -> int:
    """Decode exactly two high-word-first unsigned Modbus registers to an i32."""
    if len(words) != 2:
        raise ValueError("exactly two Modbus words are required")
    high_word, low_word = words
    if any(type(word) is not int or not 0 <= word <= 0xFFFF for word in words):
        raise ValueError("each Modbus word must be an unsigned 16-bit integer")
    raw = (high_word << 16) | low_word
    return raw - 0x1_0000_0000 if raw & 0x8000_0000 else raw


def encode_u32(value: int) -> tuple[int, int]:
    """Encode a raw unsigned 32-bit value as high-word-first Modbus words."""
    if type(value) is not int or not 0 <= value <= 0xFFFF_FFFF:
        raise ValueError("value must fit in an unsigned 32-bit integer")
    return (value >> 16) & 0xFFFF, value & 0xFFFF


def decode_u32(words: Sequence[object]) -> int:
    """Decode exactly two high-word-first Modbus registers to a raw u32."""
    if len(words) != 2:
        raise ValueError("exactly two Modbus words are required")
    high_word, low_word = words
    if any(type(word) is not int or not 0 <= word <= 0xFFFF for word in words):
        raise ValueError("each Modbus word must be an unsigned 16-bit integer")
    return (high_word << 16) | low_word
