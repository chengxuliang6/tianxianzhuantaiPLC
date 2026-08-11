"""The software-side Modbus register contract and integer codec.

The order of multi-register values is intentionally centralized here.  Hardware
writes remain disabled until the PLC word-order magic probe is verified.
"""

from __future__ import annotations

from enum import IntEnum
from typing import Sequence


EVENT_RECORD_WORDS = 6
EVENT_RECORD_COUNT = 360


class Register(IntEnum):
    """0-based Modbus holding-register addresses, equal to PLC D numbers."""

    # D1000-D1099 command and parameter block
    MODE = 1000
    DIRECTION = 1001
    SPEED_INDEX = 1002
    START_SEQ = 1003
    STOP_SEQ = 1004
    SET_ZERO_SEQ = 1005
    RESET_FAULT_SEQ = 1006
    POWER_SEQ = 1007
    HEARTBEAT = 1008
    BUFFER_ACK_SEQ = 1009
    TOTAL_RATIO_HI = 1010
    TOTAL_RATIO_LO = 1011
    ACCELERATION_HI = 1012
    ACCELERATION_LO = 1013
    DECELERATION_HI = 1014
    DECELERATION_LO = 1015
    SOFTWARE_STOP_DECELERATION_HI = 1016
    SOFTWARE_STOP_DECELERATION_LO = 1017
    BACKLASH_COMPENSATION_HI = 1018
    BACKLASH_COMPENSATION_LO = 1019

    # D1100-D1199 status and run metadata block
    RUN_STATE = 1100
    STATUS_FLAGS = 1101
    FAULT_CODE = 1102
    ACTUAL_POSITION_HI = 1103
    ACTUAL_POSITION_LO = 1104
    TARGET_POSITION_HI = 1105
    TARGET_POSITION_LO = 1106
    ACTUAL_VELOCITY_HI = 1107
    ACTUAL_VELOCITY_LO = 1108
    HEARTBEAT_ECHO = 1109
    START_ACK_SEQ = 1110
    STOP_ACK_SEQ = 1111
    SET_ZERO_ACK_SEQ = 1112
    RESET_FAULT_ACK_SEQ = 1113
    POWER_ACK_SEQ = 1114
    BUFFER_ACKED_SEQ = 1115
    EVENT_COUNT = 1116
    EVENT_GENERATION = 1117
    RUN_STATUS = 1118

    # D1200-D1299 time synchronization and protocol probe block
    PROTOCOL_VERSION = 1200
    WORD_ORDER_PROBE_HI = 1201
    WORD_ORDER_PROBE_LO = 1202
    TIME_SYNC_REQUEST_SEQ = 1203
    PLC_TICK_MS_HI = 1204
    PLC_TICK_MS_LO = 1205
    TIME_SYNC_RESPONSE_SEQ = 1206

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
