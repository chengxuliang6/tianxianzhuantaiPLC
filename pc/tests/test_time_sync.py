from __future__ import annotations

from fractions import Fraction

import pytest

from turntable_control.time_sync import ClockNotSynchronized, ClockSynchronizer, _round_half_away_from_zero


def test_clock_sync_prefers_lowest_round_trip_sample() -> None:
    sync = ClockSynchronizer()
    sync.add_sample(pc_send_ms=1000, plc_ms=500, pc_recv_ms=1040)
    sync.add_sample(pc_send_ms=2000, plc_ms=1498, pc_recv_ms=2004)

    assert sync.to_epoch_ms(1600) == 2104
    assert sync.best_sample is not None
    assert sync.best_sample.round_trip_ms == 4


def test_clock_sync_keeps_eight_lowest_rtt_samples_and_prefers_newest_tie() -> None:
    sync = ClockSynchronizer()
    for index in range(9):
        sync.add_sample(pc_send_ms=index * 100, plc_ms=0, pc_recv_ms=index * 100 + (9 - index))

    assert sync.sample_count == 8
    assert sync.best_sample is not None
    assert sync.best_sample.round_trip_ms == 1

    sync.add_sample(pc_send_ms=1000, plc_ms=0, pc_recv_ms=1001)
    assert sync.best_sample is not None
    assert sync.best_sample.pc_send_ms == 1000


def test_clock_sync_rejects_invalid_sample_fields() -> None:
    sync = ClockSynchronizer()
    for args in (
        dict(pc_send_ms=True, plc_ms=0, pc_recv_ms=1),
        dict(pc_send_ms=-1, plc_ms=0, pc_recv_ms=1),
        dict(pc_send_ms=2, plc_ms=0, pc_recv_ms=1),
        dict(pc_send_ms=0, plc_ms=0x1_0000_0000, pc_recv_ms=1),
    ):
        with pytest.raises(ValueError):
            sync.add_sample(**args)


def test_clock_sync_requires_a_sample_before_conversion() -> None:
    with pytest.raises(ClockNotSynchronized):
        ClockSynchronizer().to_epoch_ms(0)


def test_clock_sync_maps_ticks_across_u32_wrap_in_both_directions() -> None:
    sync = ClockSynchronizer()
    sync.add_sample(pc_send_ms=10_000, plc_ms=0xFFFF_FFFE, pc_recv_ms=10_000)

    assert sync.to_epoch_ms(1) == 10_003
    assert sync.to_epoch_ms(0xFFFF_FFFD) == 9_999


def test_clock_sync_rejects_half_range_delta_and_invalid_conversion_tick() -> None:
    sync = ClockSynchronizer()
    sync.add_sample(pc_send_ms=10, plc_ms=0, pc_recv_ms=10)

    with pytest.raises(ValueError):
        sync.to_epoch_ms(0x8000_0000)
    with pytest.raises(ValueError):
        sync.to_epoch_ms(False)


def test_event_mapping_wraps_run_start_before_conversion() -> None:
    sync = ClockSynchronizer()
    sync.add_sample(pc_send_ms=10_000, plc_ms=0xFFFF_FFFE, pc_recv_ms=10_000)

    assert sync.event_to_epoch_ms(0xFFFF_FFFE, 3) == 10_003
    with pytest.raises(ValueError):
        sync.event_to_epoch_ms(0, 0x8000_0000)


def test_clock_sync_rounds_midpoint_halves_away_from_zero() -> None:
    sync = ClockSynchronizer()
    sync.add_sample(pc_send_ms=1000, plc_ms=0, pc_recv_ms=1001)
    assert sync.to_epoch_ms(0) == 1001

    negative = ClockSynchronizer()
    negative.add_sample(pc_send_ms=0, plc_ms=1, pc_recv_ms=0)
    assert negative.to_epoch_ms(0) == -1


def test_clock_sync_retains_an_exact_large_half_midpoint_reviewer_regression() -> None:
    sync = ClockSynchronizer()
    sample = sync.add_sample(pc_send_ms=2**53 + 1, plc_ms=0, pc_recv_ms=2**53 + 2)

    assert sample.midpoint_ms == Fraction(2**54 + 3, 2)
    assert sample.offset_ms == Fraction(2**54 + 3, 2)
    assert sync.to_epoch_ms(0) == 2**53 + 2


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Fraction(2**80 * 2 + 1, 2), 2**80 + 1),
        (Fraction(-(2**80 * 2 + 1), 2), -(2**80 + 1)),
    ],
)
def test_half_away_rounding_is_exact_for_large_positive_and_negative_halves(value: Fraction, expected: int) -> None:
    assert _round_half_away_from_zero(value) == expected


def test_nearby_tick_conversions_are_monotonic() -> None:
    sync = ClockSynchronizer()
    sync.add_sample(pc_send_ms=1_000, plc_ms=500, pc_recv_ms=1_002)

    assert [sync.to_epoch_ms(tick) for tick in (499, 500, 501, 502)] == [1000, 1001, 1002, 1003]
