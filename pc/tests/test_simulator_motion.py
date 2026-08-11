from __future__ import annotations

import pytest

from turntable_control.domain import Direction, Mode, MotionRejected, RunState, RunStatus
from turntable_control.simulator import TurntableSimulator


@pytest.mark.parametrize(
    ("direction", "expected_position"),
    [(Direction.CW, 360.0), (Direction.CCW, -360.0)],
)
def test_auto_one_turn_finishes_exactly_with_all_degree_events(
    direction: Direction, expected_position: float
) -> None:
    sim = TurntableSimulator()
    sim.start(Mode.AUTO, direction, speed_deg_s=10.0)
    sim.run_until_stopped(step_ms=10)

    assert sim.position_deg == pytest.approx(expected_position, abs=1e-9)
    assert sim.velocity_deg_s == 0.0
    assert sim.run_state is RunState.READY
    assert sim.run_status is RunStatus.COMPLETED
    assert [event.travel_angle_deg for event in sim.events] == list(range(1, 361))
    assert [event.sequence for event in sim.events] == list(range(1, 361))
    assert all(
        event.actual_position_deg == pytest.approx(direction.value * event.travel_angle_deg)
        for event in sim.events
    )
    assert all(
        earlier.elapsed_ms <= later.elapsed_ms
        for earlier, later in zip(sim.events, sim.events[1:])
    )
    assert all(0 <= event.elapsed_ms <= sim.elapsed_ms for event in sim.events)


def test_velocity_uses_a_smooth_bounded_profile_and_decelerates_before_target() -> None:
    sim = TurntableSimulator(acceleration_deg_s2=5.0, deceleration_deg_s2=5.0)
    sim.start(Mode.MANUAL, Direction.CW, speed_deg_s=10.0)

    samples = []
    while sim.run_state is not RunState.READY:
        sim.tick(100, heartbeat_updated=True)
        samples.append((sim.position_deg, sim.velocity_deg_s))

    velocities = [velocity for _, velocity in samples]
    assert velocities[0] > 0.0
    assert max(velocities) <= 10.0
    peak = velocities.index(max(velocities))
    assert any(velocities[index] < velocities[index - 1] for index in range(peak + 1, len(velocities)))
    assert sim.position_deg == pytest.approx(360.0, abs=1e-9)
    assert sim.velocity_deg_s == 0.0


def test_auto_from_interior_position_rejects_insufficient_direction_and_opposite_succeeds() -> None:
    sim = TurntableSimulator(position_deg=10.0)
    with pytest.raises(MotionRejected, match="方向空间不足"):
        sim.start(Mode.AUTO, Direction.CW, speed_deg_s=1.0)

    sim.start(Mode.AUTO, Direction.CCW, speed_deg_s=1.0)
    sim.run_until_stopped(step_ms=50)
    assert sim.position_deg == pytest.approx(-350.0)


@pytest.mark.parametrize(
    ("position", "direction", "target"),
    [(100.0, Direction.CW, 360.0), (100.0, Direction.CCW, -260.0), (360.0, Direction.CW, 360.0), (-360.0, Direction.CCW, -360.0)],
)
def test_manual_target_is_clamped_at_the_soft_limits(
    position: float, direction: Direction, target: float
) -> None:
    sim = TurntableSimulator(position_deg=position)
    sim.start(Mode.MANUAL, direction, speed_deg_s=1.0)
    sim.run_until_stopped(step_ms=50)
    assert sim.target_deg == target
    assert sim.position_deg == pytest.approx(target)
    assert -360.0 <= sim.position_deg <= 360.0


@pytest.mark.parametrize(
    ("mode", "expected_status"),
    [(Mode.MANUAL, RunStatus.MANUAL_STOPPED), (Mode.AUTO, RunStatus.AUTOMATIC_ABORTED)],
)
def test_request_stop_brakes_motion_without_exceeding_braking_bound(
    mode: Mode, expected_status: RunStatus
) -> None:
    sim = TurntableSimulator()
    sim.start(mode, Direction.CW, speed_deg_s=10.0)
    sim.tick(2_000, heartbeat_updated=True)
    velocity_at_request = sim.velocity_deg_s
    position_at_request = sim.position_deg
    sim.request_stop()
    sim.run_until_stopped(step_ms=10)

    assert sim.run_status is expected_status
    assert sim.velocity_deg_s == 0.0
    assert sim.position_deg - position_at_request <= velocity_at_request**2 / 20.0 + 0.02


@pytest.mark.parametrize("mode", [Mode.MANUAL, Mode.AUTO])
def test_heartbeat_timeout_aborts_both_modes_and_stops(mode: Mode) -> None:
    sim = TurntableSimulator()
    sim.start(mode, Direction.CW, speed_deg_s=1.0)
    sim.tick(1_001, heartbeat_updated=False)

    assert sim.run_status is RunStatus.COMMUNICATION_ABORTED
    sim.run_until_stopped(step_ms=10)
    assert sim.velocity_deg_s == 0.0
    assert sim.run_status is RunStatus.COMMUNICATION_ABORTED


def test_exactly_one_second_without_heartbeat_does_not_abort_but_1001_ms_does() -> None:
    sim = TurntableSimulator()
    sim.start(Mode.AUTO, Direction.CW, speed_deg_s=1.0)
    sim.tick(1_000, heartbeat_updated=False)
    assert sim.run_status is RunStatus.RUNNING
    sim.tick(1, heartbeat_updated=False)
    assert sim.run_status is RunStatus.COMMUNICATION_ABORTED


def test_event_buffer_requires_acknowledgement_before_a_new_run() -> None:
    sim = TurntableSimulator()
    sim.start(Mode.MANUAL, Direction.CW, speed_deg_s=10.0)
    sim.tick(1_000, heartbeat_updated=True)
    sim.request_stop()
    sim.run_until_stopped()
    assert sim.events
    with pytest.raises(MotionRejected, match="acknowledge"):
        sim.start(Mode.MANUAL, Direction.CCW, speed_deg_s=1.0)

    sim.acknowledge_events()
    assert sim.events == []
    sim.start(Mode.MANUAL, Direction.CCW, speed_deg_s=1.0)


def test_invalid_commands_and_ticks_are_rejected_and_repeated_runs_are_deterministic() -> None:
    sim = TurntableSimulator()
    with pytest.raises(MotionRejected):
        sim.start(Mode.AUTO, Direction.CW, speed_deg_s=3.0)
    with pytest.raises(ValueError, match="positive"):
        sim.tick(0)

    sim.start(Mode.AUTO, Direction.CW, speed_deg_s=1.0)
    with pytest.raises(MotionRejected, match="running"):
        sim.start(Mode.AUTO, Direction.CW, speed_deg_s=1.0)
    sim.request_stop()
    sim.run_until_stopped()
    sim.acknowledge_events()

    first = TurntableSimulator()
    second = TurntableSimulator()
    for candidate in (first, second):
        candidate.start(Mode.AUTO, Direction.CW, speed_deg_s=10.0)
        candidate.run_until_stopped(step_ms=17)
    assert first.position_deg == second.position_deg
    assert first.elapsed_ms == second.elapsed_ms
    assert first.events == second.events
