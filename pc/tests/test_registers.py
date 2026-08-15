from __future__ import annotations

import pytest

from turntable_control import registers
from turntable_control.registers import (
    Register,
    decode_i32,
    encode_i32,
)


@pytest.mark.parametrize(
    "value",
    [-360000, -1, 0, 1, 360000, 0x12345678, -(2**31), 2**31 - 1],
)
def test_i32_round_trip_uses_high_word_first(value: int) -> None:
    assert decode_i32(encode_i32(value)) == value


def test_i32_magic_probe_is_encoded_high_word_first() -> None:
    assert encode_i32(0x12345678) == (0x1234, 0x5678)


@pytest.mark.parametrize("value", [0, 1, 0x7FFF_FFFF, 0x8000_0000, 0xFFFF_FFFF])
def test_u32_round_trip_preserves_tick_bit_patterns_past_signed_range(value: int) -> None:
    encode = getattr(registers, "encode_u32", None)
    decode = getattr(registers, "decode_u32", None)
    assert callable(encode) and callable(decode)
    assert decode(encode(value)) == value


def test_u32_tick_uses_high_word_first_without_signed_conversion() -> None:
    encode = getattr(registers, "encode_u32", None)
    assert callable(encode)
    assert encode(0xFEDC_BA98) == (0xFEDC, 0xBA98)


@pytest.mark.parametrize("value", [-1, 0x1_0000_0000, True, 1.0])
def test_encode_u32_rejects_non_u32_values(value: object) -> None:
    encode = getattr(registers, "encode_u32", None)
    assert callable(encode)
    with pytest.raises(ValueError):
        encode(value)


@pytest.mark.parametrize("words", [(), (1,), (1, 2, 3), (-1, 0), (0, 0x10000), (True, 0)])
def test_decode_u32_requires_two_legal_unsigned_words(words: tuple[object, ...]) -> None:
    decode = getattr(registers, "decode_u32", None)
    assert callable(decode)
    with pytest.raises(ValueError):
        decode(words)


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
    assert all(0 <= address <= 206 or address == Register.EVENT_BUFFER_BASE for address in Register)
    assert not ({*range(1000, 1207)} & {int(address) for address in Register})
    assert 360 not in set(Register)


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
        Register.RUN_START_TICK_MS_HI,
        Register.RUN_START_TICK_MS_LO,
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

    assert command_addresses == list(range(0, 20))
    assert status_addresses == list(range(100, 121))
    assert protocol_addresses == list(range(200, 207))
    assert len(command_addresses + status_addresses + protocol_addresses) == len(
        set(command_addresses + status_addresses + protocol_addresses)
    )
    assert Register.EVENT_BUFFER_BASE == 2000
    assert registers.EVENT_RECORD_WORDS == 6
    assert registers.EVENT_RECORD_COUNT == 360
    assert Register.event_last_address() == 4159


def test_run_start_tick_occupies_the_next_two_status_words_without_overlap() -> None:
    assert Register.RUN_START_TICK_MS_HI == 119
    assert Register.RUN_START_TICK_MS_LO == 120
    assert Register.RUN_START_TICK_MS_LO < Register.PROTOCOL_VERSION
