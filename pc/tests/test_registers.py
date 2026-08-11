from __future__ import annotations

import pytest

from turntable_control import registers
from turntable_control.registers import Register, decode_i32, encode_i32


@pytest.mark.parametrize(
    "value",
    [-360000, -1, 0, 1, 360000, 0x12345678, -(2**31), 2**31 - 1],
)
def test_i32_round_trip_uses_high_word_first(value: int) -> None:
    assert decode_i32(encode_i32(value)) == value


def test_i32_magic_probe_is_encoded_high_word_first() -> None:
    assert encode_i32(0x12345678) == (0x1234, 0x5678)


@pytest.mark.parametrize("value", [-(2**31) - 1, 2**31])
def test_encode_i32_rejects_values_outside_signed_i32(value: int) -> None:
    with pytest.raises(ValueError):
        encode_i32(value)


@pytest.mark.parametrize("words", [(), (1,), (1, 2, 3)])
def test_decode_i32_requires_exactly_two_words(words: tuple[int, ...]) -> None:
    with pytest.raises(ValueError):
        decode_i32(words)


@pytest.mark.parametrize(
    "words",
    [(-1, 0), (0x10000, 0), (0, -1), (0, 0x10000), (1.0, 0), (0, "1"), (True, 0)],
)
def test_decode_i32_rejects_malformed_unsigned_16_bit_words(words: tuple[object, object]) -> None:
    with pytest.raises(ValueError):
        decode_i32(words)


def test_register_iteration_contains_only_allocated_modbus_addresses() -> None:
    assert all(1000 <= address <= 1299 or address == Register.EVENT_BUFFER_BASE for address in Register)
    assert {6, 360}.isdisjoint(set(Register))


def test_register_ranges_are_non_overlapping_and_event_buffer_ends_at_d4159() -> None:
    command_addresses = [
        Register.MODE,
        Register.DIRECTION,
        Register.SPEED_INDEX,
        Register.START_SEQ,
        Register.STOP_SEQ,
        Register.SET_ZERO_SEQ,
        Register.RESET_FAULT_SEQ,
        Register.POWER_SEQ,
        Register.HEARTBEAT,
        Register.BUFFER_ACK_SEQ,
        Register.TOTAL_RATIO_HI,
        Register.TOTAL_RATIO_LO,
        Register.ACCELERATION_HI,
        Register.ACCELERATION_LO,
        Register.DECELERATION_HI,
        Register.DECELERATION_LO,
        Register.SOFTWARE_STOP_DECELERATION_HI,
        Register.SOFTWARE_STOP_DECELERATION_LO,
        Register.BACKLASH_COMPENSATION_HI,
        Register.BACKLASH_COMPENSATION_LO,
    ]
    status_addresses = [
        Register.RUN_STATE,
        Register.STATUS_FLAGS,
        Register.FAULT_CODE,
        Register.ACTUAL_POSITION_HI,
        Register.ACTUAL_POSITION_LO,
        Register.TARGET_POSITION_HI,
        Register.TARGET_POSITION_LO,
        Register.ACTUAL_VELOCITY_HI,
        Register.ACTUAL_VELOCITY_LO,
        Register.HEARTBEAT_ECHO,
        Register.START_ACK_SEQ,
        Register.STOP_ACK_SEQ,
        Register.SET_ZERO_ACK_SEQ,
        Register.RESET_FAULT_ACK_SEQ,
        Register.POWER_ACK_SEQ,
        Register.BUFFER_ACKED_SEQ,
        Register.EVENT_COUNT,
        Register.EVENT_GENERATION,
        Register.RUN_STATUS,
    ]
    protocol_addresses = [
        Register.PROTOCOL_VERSION,
        Register.WORD_ORDER_PROBE_HI,
        Register.WORD_ORDER_PROBE_LO,
        Register.TIME_SYNC_REQUEST_SEQ,
        Register.PLC_TICK_MS_HI,
        Register.PLC_TICK_MS_LO,
        Register.TIME_SYNC_RESPONSE_SEQ,
    ]

    assert all(1000 <= address <= 1099 for address in command_addresses)
    assert all(1100 <= address <= 1199 for address in status_addresses)
    assert all(1200 <= address <= 1299 for address in protocol_addresses)
    assert len(command_addresses + status_addresses + protocol_addresses) == len(
        set(command_addresses + status_addresses + protocol_addresses)
    )
    assert Register.EVENT_BUFFER_BASE == 2000
    assert registers.EVENT_RECORD_WORDS == 6
    assert registers.EVENT_RECORD_COUNT == 360
    assert Register.event_last_address() == 4159
