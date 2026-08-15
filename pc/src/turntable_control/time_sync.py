"""Convert raw PLC millisecond ticks to PC epoch milliseconds.

The estimator uses the lowest observed request/response round trip.  PLC tick
fields have 1 ms resolution; this is an offset estimate, not an absolute-time
guarantee.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction


_U32_MAX = 0xFFFF_FFFF
_U32_MODULUS = 0x1_0000_0000
_HALF_RANGE = 0x8000_0000


class ClockNotSynchronized(RuntimeError):
    """Raised when conversion is requested before a clock sample is available."""


@dataclass(frozen=True)
class ClockSample:
    """One midpoint-based observation of the PLC-to-PC time offset."""

    plc_ms: int
    pc_send_ms: int
    pc_recv_ms: int
    midpoint_ms: Fraction
    round_trip_ms: int
    offset_ms: Fraction


class ClockSynchronizer:
    """Bounded, deterministic clock-offset estimator for raw u32 PLC ticks."""

    def __init__(self, max_samples: int = 8) -> None:
        if type(max_samples) is not int or not 1 <= max_samples <= 8:
            raise ValueError("max_samples must be an integer in 1..8")
        self._max_samples = max_samples
        self._samples: list[ClockSample] = []

    @property
    def best_sample(self) -> ClockSample | None:
        return self._samples[0] if self._samples else None

    @property
    def sample_count(self) -> int:
        return len(self._samples)

    def reset(self) -> None:
        """Discard samples that belong to a prior PLC lifetime."""
        self._samples.clear()

    def add_sample(self, pc_send_ms: int, plc_ms: int, pc_recv_ms: int) -> ClockSample:
        """Record a request/response observation and return its immutable sample."""
        _validate_epoch_ms(pc_send_ms, "pc_send_ms")
        _validate_u32(plc_ms, "plc_ms")
        _validate_epoch_ms(pc_recv_ms, "pc_recv_ms")
        if pc_recv_ms < pc_send_ms:
            raise ValueError("pc_recv_ms must not precede pc_send_ms")

        midpoint_ms = Fraction(pc_send_ms + pc_recv_ms, 2)
        sample = ClockSample(
            plc_ms=plc_ms,
            pc_send_ms=pc_send_ms,
            pc_recv_ms=pc_recv_ms,
            midpoint_ms=midpoint_ms,
            round_trip_ms=pc_recv_ms - pc_send_ms,
            offset_ms=midpoint_ms - plc_ms,
        )
        # Python's stable sort makes a newly appended equal-RTT sample win.
        self._samples = sorted([sample, *self._samples], key=lambda item: item.round_trip_ms)[: self._max_samples]
        return sample

    def to_epoch_ms(self, plc_ms: int) -> int:
        """Map one raw u32 PLC tick through the best available sample."""
        _validate_u32(plc_ms, "plc_ms")
        sample = self.best_sample
        if sample is None:
            raise ClockNotSynchronized("no PLC/PC clock sample is available")
        delta_ms = _signed_u32_delta(plc_ms, sample.plc_ms)
        return _round_half_away_from_zero(sample.midpoint_ms + delta_ms)

    def event_to_epoch_ms(self, run_start_plc_ms: int, elapsed_ms: int) -> int:
        """Map an event relative to a run start, correctly crossing u32 wrap."""
        _validate_u32(run_start_plc_ms, "run_start_plc_ms")
        _validate_u32(elapsed_ms, "elapsed_ms")
        if elapsed_ms >= _HALF_RANGE:
            raise ValueError("elapsed_ms must be less than the u32 half-range")
        return self.to_epoch_ms((run_start_plc_ms + elapsed_ms) & _U32_MAX)


def _validate_epoch_ms(value: int, field: str) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"{field} must be a non-negative integer epoch millisecond value")


def _validate_u32(value: int, field: str) -> None:
    if type(value) is not int or not 0 <= value <= _U32_MAX:
        raise ValueError(f"{field} must be a raw unsigned 32-bit integer")


def _signed_u32_delta(value: int, reference: int) -> int:
    delta = (value - reference) & _U32_MAX
    if delta == _HALF_RANGE:
        raise ValueError("PLC tick delta is ambiguous at the u32 half-range")
    return delta - _U32_MODULUS if delta >= _HALF_RANGE else delta


def _round_half_away_from_zero(value: Fraction | int) -> int:
    """Round to the nearest millisecond; exact halves go away from zero."""
    fraction = Fraction(value)
    magnitude, remainder = divmod(abs(fraction.numerator), fraction.denominator)
    if remainder * 2 >= fraction.denominator:
        magnitude += 1
    return magnitude if fraction.numerator >= 0 else -magnitude
