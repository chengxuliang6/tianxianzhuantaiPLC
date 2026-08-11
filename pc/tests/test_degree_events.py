from __future__ import annotations

from turntable_control.domain import Direction
from turntable_control.simulator import crossed_degree_events


def test_cw_crossing_never_drops_intermediate_degrees() -> None:
    assert crossed_degree_events(0.2, 3.4, 0.0, Direction.CW) == [1, 2, 3]


def test_ccw_crossings_are_positive_travel_angles() -> None:
    assert crossed_degree_events(10.0, 7.8, 10.0, Direction.CCW) == [1, 2]


def test_no_progress_or_backtracking_never_emits_events() -> None:
    assert crossed_degree_events(2.4, 2.4, 0.0, Direction.CW) == []
    assert crossed_degree_events(2.4, 1.8, 0.0, Direction.CW) == []


def test_crossings_start_after_the_prior_integer_without_duplicates() -> None:
    assert crossed_degree_events(0.0, 1.0, 0.0, Direction.CW) == [1]
    assert crossed_degree_events(1.0, 1.9, 0.0, Direction.CW) == []
    assert crossed_degree_events(359.9, 361.0, 0.0, Direction.CW) == [360]
