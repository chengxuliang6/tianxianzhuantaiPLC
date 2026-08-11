"""Pure turntable motion rules with no PLC or network dependency."""

from __future__ import annotations

from enum import Enum, IntEnum


SOFT_MIN_DEG = -360.0
SOFT_MAX_DEG = 360.0
SPEEDS_DEG_S = (1.0, 2.0, 4.0, 5.0, 10.0)


class Mode(IntEnum):
    MANUAL = 0
    AUTO = 1


class Direction(IntEnum):
    CW = 1
    CCW = -1


class RunState(IntEnum):
    INITIALIZING = 0
    ZERO_REQUIRED = 1
    READY = 2
    MANUAL_RUNNING = 3
    AUTO_RUNNING = 4
    STOPPING = 5
    AUTO_ABORTED = 6
    FAULT = 7


class RunStatus(IntEnum):
    IDLE = 0
    RUNNING = 1
    COMPLETED = 2
    MANUAL_STOPPED = 3
    AUTOMATIC_ABORTED = 4
    COMMUNICATION_ABORTED = 5
    FAULTED = 6


class MotionRejected(ValueError):
    """Raised when the requested motion cannot remain within the soft range."""


def automatic_target(position_deg: float, direction: Direction) -> float:
    """Return one exact turn in ``direction`` or reject a limit-crossing request."""
    target = position_deg + 360.0 * direction.value
    if not SOFT_MIN_DEG <= target <= SOFT_MAX_DEG:
        raise MotionRejected("该方向空间不足，请反向运行")
    return target


def manual_target(position_deg: float, direction: Direction) -> float:
    """Return up to one turn in ``direction``, stopping at the global soft limit."""
    target = position_deg + 360.0 * direction.value
    return max(SOFT_MIN_DEG, min(SOFT_MAX_DEG, target))


def motor_rpm(speed_deg_s: float, total_ratio: float = 50.0) -> float:
    """Convert turntable angular speed to motor revolutions per minute."""
    return speed_deg_s / 360.0 * 60.0 * total_ratio
