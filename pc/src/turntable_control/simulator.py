"""Deterministic, hardware-free model of the turntable motion contract."""

from __future__ import annotations

from dataclasses import dataclass
import math

from .domain import (
    Direction,
    Mode,
    MotionRejected,
    RunState,
    RunStatus,
    SPEEDS_DEG_S,
    SOFT_MAX_DEG,
    SOFT_MIN_DEG,
    automatic_target,
    manual_target,
)


_EPSILON = 1e-12


@dataclass(frozen=True)
class DegreeEvent:
    """One integer travel-angle crossing in the current motion run."""

    sequence: int
    travel_angle_deg: int
    actual_position_deg: float
    elapsed_ms: int


def crossed_degree_events(
    previous: float, current: float, origin: float, direction: Direction
) -> list[int]:
    """Return each newly crossed integer travel angle, in motion order."""
    previous_travel = (previous - origin) * direction.value
    current_travel = (current - origin) * direction.value
    first = max(1, math.floor(previous_travel) + 1)
    last = min(360, math.floor(current_travel))
    return list(range(first, last + 1)) if last >= first else []


class TurntableSimulator:
    """Simulate the Task 2 motion state machine using a 1 ms fixed time base."""

    def __init__(
        self,
        position_deg: float = 0.0,
        acceleration_deg_s2: float = 5.0,
        deceleration_deg_s2: float = 5.0,
        stop_deceleration_deg_s2: float = 10.0,
        heartbeat_timeout_ms: int = 1000,
    ) -> None:
        if not SOFT_MIN_DEG <= position_deg <= SOFT_MAX_DEG:
            raise MotionRejected("initial position must be within the soft limits")
        if any(value <= 0.0 for value in (acceleration_deg_s2, deceleration_deg_s2, stop_deceleration_deg_s2)):
            raise ValueError("all acceleration values must be positive")
        if heartbeat_timeout_ms <= 0:
            raise ValueError("heartbeat timeout must be positive")

        self.position_deg = float(position_deg)
        self.velocity_deg_s = 0.0
        self.run_state = RunState.READY
        self.run_status = RunStatus.IDLE
        self.events: list[DegreeEvent] = []
        self.elapsed_ms = 0
        self.target_deg = float(position_deg)
        self.mode: Mode | None = None
        self.direction: Direction | None = None

        self._acceleration_deg_s2 = float(acceleration_deg_s2)
        self._deceleration_deg_s2 = float(deceleration_deg_s2)
        self._stop_deceleration_deg_s2 = float(stop_deceleration_deg_s2)
        self._heartbeat_timeout_ms = heartbeat_timeout_ms
        self._heartbeat_age_ms = 0
        self._origin_deg = self.position_deg
        self._requested_speed_deg_s = 0.0
        self._stop_status: RunStatus | None = None
        self._buffer_pending = False

    def start(self, mode: Mode, direction: Direction, speed_deg_s: float) -> None:
        """Start one manual or automatic run, respecting the fixed speed set."""
        if self._is_active:
            raise MotionRejected("simulator is already running")
        if self._buffer_pending:
            raise MotionRejected("acknowledge buffered events before starting a new run")
        if mode not in (Mode.MANUAL, Mode.AUTO) or direction not in (Direction.CW, Direction.CCW):
            raise MotionRejected("invalid mode or direction")
        if speed_deg_s not in SPEEDS_DEG_S:
            raise MotionRejected("unsupported speed")

        target = automatic_target(self.position_deg, direction) if mode is Mode.AUTO else manual_target(self.position_deg, direction)
        self.mode = mode
        self.direction = direction
        self.target_deg = target
        self._origin_deg = self.position_deg
        self._requested_speed_deg_s = float(speed_deg_s)
        self.elapsed_ms = 0
        self._heartbeat_age_ms = 0
        self._stop_status = None
        self.run_status = RunStatus.RUNNING

        if abs(self.target_deg - self.position_deg) <= _EPSILON:
            self.velocity_deg_s = 0.0
            self.run_state = RunState.READY
            self.run_status = RunStatus.COMPLETED
            return
        self.run_state = RunState.AUTO_RUNNING if mode is Mode.AUTO else RunState.MANUAL_RUNNING

    def tick(self, delta_ms: int, heartbeat_updated: bool = True) -> None:
        """Advance the model in deterministic 1 ms increments."""
        if type(delta_ms) is not int or delta_ms <= 0:
            raise ValueError("delta_ms must be a positive integer")
        if not self._is_active:
            return
        if heartbeat_updated:
            self._heartbeat_age_ms = 0

        for _ in range(delta_ms):
            if not self._is_active:
                break
            if not heartbeat_updated:
                self._heartbeat_age_ms += 1
                if self._heartbeat_age_ms > self._heartbeat_timeout_ms:
                    self._begin_stop(RunStatus.COMMUNICATION_ABORTED)
            elapsed_before = self.elapsed_ms
            previous_position = self.position_deg
            self._advance_one_millisecond()
            self._record_crossings(previous_position, elapsed_before)
            self.elapsed_ms += 1

    def request_stop(self) -> None:
        """Request a controlled software stop; it is never an instantaneous stop."""
        if not self._is_active:
            return
        status = RunStatus.MANUAL_STOPPED if self.mode is Mode.MANUAL else RunStatus.AUTOMATIC_ABORTED
        self._begin_stop(status)

    def run_until_stopped(self, step_ms: int = 10, max_duration_ms: int = 500_000) -> None:
        """Run with healthy heartbeats until the current motion reaches standstill."""
        if type(step_ms) is not int or step_ms <= 0:
            raise ValueError("step_ms must be a positive integer")
        if type(max_duration_ms) is not int or max_duration_ms <= 0:
            raise ValueError("max_duration_ms must be a positive integer")
        spent_ms = 0
        while self._is_active and spent_ms < max_duration_ms:
            increment = min(step_ms, max_duration_ms - spent_ms)
            self.tick(increment, heartbeat_updated=True)
            spent_ms += increment
        if self._is_active:
            raise TimeoutError("simulator did not stop within max_duration_ms")

    def acknowledge_events(self) -> None:
        """Release completed-run events after a successful external save."""
        if self._is_active:
            raise MotionRejected("events can only be acknowledged while not running")
        self.events.clear()
        self._buffer_pending = False

    @property
    def _is_active(self) -> bool:
        return self.run_state in (RunState.MANUAL_RUNNING, RunState.AUTO_RUNNING, RunState.STOPPING)

    def _begin_stop(self, status: RunStatus) -> None:
        self.run_state = RunState.STOPPING
        self._stop_status = status
        self.run_status = status
        if abs(self.velocity_deg_s) <= _EPSILON:
            self._finish_stop()

    def _advance_one_millisecond(self) -> None:
        if self.run_state is RunState.STOPPING:
            self._advance_stopping(0.001)
        else:
            self._advance_planned_motion(0.001)

    def _advance_planned_motion(self, seconds: float) -> None:
        assert self.direction is not None
        remaining = (self.target_deg - self.position_deg) * self.direction.value
        if remaining <= _EPSILON:
            self.position_deg = self.target_deg
            self.velocity_deg_s = 0.0
            self._finish_completed()
            return

        speed = self.velocity_deg_s * self.direction.value
        braking_distance = speed * speed / (2.0 * self._deceleration_deg_s2)
        if remaining <= braking_distance + _EPSILON:
            acceleration = -self._deceleration_deg_s2
        elif speed < self._requested_speed_deg_s:
            acceleration = self._acceleration_deg_s2
        else:
            acceleration = 0.0

        next_speed = max(0.0, min(self._requested_speed_deg_s, speed + acceleration * seconds))
        displacement = (speed + next_speed) * 0.5 * seconds
        if displacement >= remaining - _EPSILON:
            self.position_deg = self.target_deg
            self.velocity_deg_s = 0.0
            self._finish_completed()
            return

        self.position_deg += self.direction.value * displacement
        self.velocity_deg_s = self.direction.value * next_speed

    def _advance_stopping(self, seconds: float) -> None:
        if self.direction is None:
            self._finish_stop()
            return
        speed = abs(self.velocity_deg_s)
        if speed <= _EPSILON:
            self._finish_stop()
            return
        direction_sign = 1.0 if self.velocity_deg_s >= 0.0 else -1.0
        stop_time = min(seconds, speed / self._stop_deceleration_deg_s2)
        next_speed = max(0.0, speed - self._stop_deceleration_deg_s2 * stop_time)
        displacement = (speed + next_speed) * 0.5 * stop_time
        candidate = self.position_deg + direction_sign * displacement
        if (candidate - self.target_deg) * direction_sign >= 0.0:
            candidate = self.target_deg
            next_speed = 0.0
        self.position_deg = min(SOFT_MAX_DEG, max(SOFT_MIN_DEG, candidate))
        self.velocity_deg_s = direction_sign * next_speed
        if next_speed <= _EPSILON:
            self.velocity_deg_s = 0.0
            self._finish_stop()

    def _record_crossings(self, previous_position: float, elapsed_before: int) -> None:
        assert self.direction is not None
        for travel_angle in crossed_degree_events(
            previous_position, self.position_deg, self._origin_deg, self.direction
        ):
            if len(self.events) >= 360:
                raise RuntimeError("degree event buffer is full")
            crossing_position = self._origin_deg + self.direction.value * travel_angle
            distance_in_step = abs(self.position_deg - previous_position)
            if distance_in_step <= _EPSILON:
                fraction = 1.0
            else:
                fraction = abs(crossing_position - previous_position) / distance_in_step
            interpolated_ms = elapsed_before + round(min(1.0, max(0.0, fraction)))
            if self.events:
                interpolated_ms = max(interpolated_ms, self.events[-1].elapsed_ms)
            self.events.append(
                DegreeEvent(
                    sequence=len(self.events) + 1,
                    travel_angle_deg=travel_angle,
                    actual_position_deg=crossing_position,
                    elapsed_ms=interpolated_ms,
                )
            )
            self._buffer_pending = True

    def _finish_completed(self) -> None:
        self.run_state = RunState.READY
        self.run_status = RunStatus.COMPLETED

    def _finish_stop(self) -> None:
        self.run_state = RunState.READY
        self.velocity_deg_s = 0.0
        assert self._stop_status is not None
        self.run_status = self._stop_status
