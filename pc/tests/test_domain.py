from __future__ import annotations

import pytest

from turntable_control.domain import (
    Direction,
    MotionRejected,
    SOFT_MAX_DEG,
    SOFT_MIN_DEG,
    SPEEDS_DEG_S,
    automatic_target,
    manual_target,
    motor_rpm,
)


def test_soft_limits_are_exactly_one_continuous_turn_each_side() -> None:
    assert (SOFT_MIN_DEG, SOFT_MAX_DEG) == (-360.0, 360.0)


def test_direction_values_match_motion_signs() -> None:
    assert Direction.CW.value == 1
    assert Direction.CCW.value == -1


@pytest.mark.parametrize(
    ("position_deg", "direction", "expected"),
    [
        (0.0, Direction.CW, 360.0),
        (0.0, Direction.CCW, -360.0),
        (100.0, Direction.CW, 360.0),
        (100.0, Direction.CCW, -260.0),
        (360.0, Direction.CW, 360.0),
        (360.0, Direction.CCW, 0.0),
        (-360.0, Direction.CW, 0.0),
        (-360.0, Direction.CCW, -360.0),
    ],
)
def test_manual_target_clamps_one_turn_to_global_limit(
    position_deg: float, direction: Direction, expected: float
) -> None:
    assert manual_target(position_deg, direction) == expected


@pytest.mark.parametrize(
    ("position_deg", "direction", "expected"),
    [
        (0.0, Direction.CW, 360.0),
        (0.0, Direction.CCW, -360.0),
        (360.0, Direction.CCW, 0.0),
        (-360.0, Direction.CW, 0.0),
    ],
)
def test_automatic_target_allows_exactly_bounded_full_turns(
    position_deg: float, direction: Direction, expected: float
) -> None:
    assert automatic_target(position_deg, direction) == expected


@pytest.mark.parametrize(
    ("position_deg", "direction"),
    [(100.0, Direction.CW), (-100.0, Direction.CCW), (360.0, Direction.CW), (-360.0, Direction.CCW)],
)
def test_automatic_target_rejects_when_direction_has_insufficient_space(
    position_deg: float, direction: Direction
) -> None:
    with pytest.raises(MotionRejected, match="该方向空间不足，请反向运行"):
        automatic_target(position_deg, direction)


@pytest.mark.parametrize(
    ("speed_deg_s", "expected_rpm"),
    [
        (1.0, 8.333333333333334),
        (2.0, 16.666666666666668),
        (4.0, 33.333333333333336),
        (5.0, 41.666666666666664),
        (10.0, 83.33333333333333),
    ],
)
def test_motor_rpm_uses_turntable_speed_and_total_ratio(
    speed_deg_s: float, expected_rpm: float
) -> None:
    assert motor_rpm(speed_deg_s) == pytest.approx(expected_rpm)


def test_available_speeds_are_fixed() -> None:
    assert SPEEDS_DEG_S == (1.0, 2.0, 4.0, 5.0, 10.0)
