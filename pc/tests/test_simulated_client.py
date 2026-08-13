from __future__ import annotations

from dataclasses import dataclass

import pytest

from turntable_control.controller import (
    STATUS_BUFFER_READY,
    STATUS_POWERED,
    STATUS_SOFT_LIMIT,
    STATUS_ZERO_VALID,
)
from turntable_control.domain import Direction, Mode, MotionRejected, RunState, RunStatus
from turntable_control.modbus_client import CommunicationError


@dataclass
class FakeMonotonicClock:
    milliseconds: int = 0

    def now(self) -> int:
        return self.milliseconds

    def advance(self, milliseconds: int) -> None:
        self.milliseconds += milliseconds


def commissioned_client(clock: FakeMonotonicClock, **kwargs):
    from turntable_control.simulated_client import SimulatedTurntableClient

    client = SimulatedTurntableClient(monotonic_ms=clock.now, **kwargs)
    client.connect()
    client.set_zero()
    client.toggle_power()
    return client


def run_with_heartbeats(client, clock: FakeMonotonicClock, *, step_ms: int = 250):
    heartbeat = 0
    for _ in range(2_000):
        clock.advance(step_ms)
        status = client.read_status()
        if status.run_state is RunState.READY and status.run_status is not RunStatus.RUNNING:
            return status
        heartbeat = (heartbeat + 1) & 0xFFFF
        client.write_heartbeat(heartbeat)
    raise AssertionError("simulated run did not reach a terminal state")


def test_simulated_client_adapter_is_available() -> None:
    from turntable_control.simulated_client import SimulatedTurntableClient

    assert callable(SimulatedTurntableClient)


def test_client_lifecycle_commissioning_sequences_and_protocol_snapshot() -> None:
    from turntable_control.simulated_client import SimulatedTurntableClient

    clock = FakeMonotonicClock()
    client = SimulatedTurntableClient(
        monotonic_ms=clock.now,
        initial_plc_tick_ms=0xFFFF_FFFE,
    )
    with pytest.raises(CommunicationError):
        client.read_status()

    client.connect()
    initial = client.read_status()
    assert initial.run_state is RunState.ZERO_REQUIRED
    assert initial.status_flags & (STATUS_ZERO_VALID | STATUS_POWERED) == 0
    assert (initial.protocol_version, initial.word_order_probe) == (1, 0x12345678)

    assert client.set_zero() == 1
    assert client.toggle_power() == 1
    assert client.reset_alarm() == 1
    assert client.request_time_sync() == 1
    clock.advance(3)
    ready = client.read_status()
    assert ready.run_state is RunState.READY
    assert ready.status_flags & STATUS_ZERO_VALID
    assert ready.status_flags & STATUS_POWERED
    assert ready.set_zero_ack_seq == 1
    assert ready.power_ack_seq == 1
    assert ready.reset_fault_ack_seq == 1
    assert ready.time_sync_request_seq == ready.time_sync_response_seq == 1
    assert ready.plc_tick_ms == 1

    client.close()
    client.close()
    with pytest.raises(CommunicationError):
        client.send_stop()


def test_start_uses_fixed_speed_index_and_retains_exact_events_until_ack() -> None:
    clock = FakeMonotonicClock()
    client = commissioned_client(clock)

    assert client.send_start(Mode.AUTO, Direction.CCW, 5) == 1
    assert client.start_command_seq == 1
    terminal = run_with_heartbeats(client, clock)

    assert terminal.run_state is RunState.READY
    assert terminal.run_status is RunStatus.COMPLETED
    assert terminal.actual_position_deg == pytest.approx(-360.0)
    assert terminal.target_position_deg == pytest.approx(-360.0)
    assert terminal.actual_velocity_deg_s == 0.0
    assert terminal.event_count == 360
    assert terminal.event_generation == 1
    assert terminal.status_flags & STATUS_BUFFER_READY
    assert terminal.status_flags & STATUS_SOFT_LIMIT
    events = client.read_events(360)
    assert [event.sequence for event in events] == list(range(1, 361))
    assert [event.travel_angle_deg for event in events] == list(range(1, 361))
    assert [event.actual_position_deg for event in events] == pytest.approx(
        [-float(angle) for angle in range(1, 361)]
    )
    assert all(
        earlier.elapsed_ms <= later.elapsed_ms
        for earlier, later in zip(events, events[1:])
    )

    assert client.read_status().event_count == 360
    assert client.acknowledge_buffer() == 1
    released = client.read_status()
    assert released.event_count == 0
    assert not released.status_flags & STATUS_BUFFER_READY
    assert released.event_generation == 1


def test_start_requires_zero_power_ready_no_buffer_and_exact_inputs() -> None:
    from turntable_control.simulated_client import SimulatedTurntableClient

    clock = FakeMonotonicClock()
    client = SimulatedTurntableClient(monotonic_ms=clock.now)
    client.connect()
    with pytest.raises(MotionRejected, match="未设零"):
        client.send_start(Mode.AUTO, Direction.CW, 1)
    client.set_zero()
    with pytest.raises(MotionRejected, match="伺服"):
        client.send_start(Mode.AUTO, Direction.CW, 1)
    client.toggle_power()
    for arguments in (
        (1, Direction.CW, 1),
        (Mode.AUTO, 1, 1),
        (Mode.AUTO, Direction.CW, 0),
        (Mode.AUTO, Direction.CW, 6),
    ):
        with pytest.raises((ValueError, MotionRejected)):
            client.send_start(*arguments)  # type: ignore[arg-type]

    client.send_start(Mode.MANUAL, Direction.CW, 4)
    with pytest.raises(MotionRejected, match="运行"):
        client.send_start(Mode.AUTO, Direction.CW, 1)
    clock.advance(2_000)
    client.write_heartbeat(1)
    client.send_stop()
    clock.advance(1_000)
    stopped = client.read_status()
    assert stopped.status_flags & STATUS_BUFFER_READY
    with pytest.raises(MotionRejected, match="数据"):
        client.send_start(Mode.MANUAL, Direction.CCW, 1)


@pytest.mark.parametrize(
    ("mode", "expected_status"),
    [
        (Mode.MANUAL, RunStatus.MANUAL_STOPPED),
        (Mode.AUTO, RunStatus.AUTOMATIC_ABORTED),
    ],
)
def test_software_stop_is_smooth_and_preserves_partial_prefix(
    mode: Mode, expected_status: RunStatus
) -> None:
    clock = FakeMonotonicClock()
    client = commissioned_client(clock)
    client.send_start(mode, Direction.CW, 5)
    for heartbeat in range(1, 9):
        clock.advance(250)
        client.write_heartbeat(heartbeat)

    before = client.read_status()
    client.send_stop()
    stopping = client.read_status()
    assert stopping.run_state is RunState.STOPPING
    assert stopping.run_status is expected_status
    assert stopping.actual_velocity_deg_s == pytest.approx(before.actual_velocity_deg_s)
    assert stopping.actual_velocity_deg_s > 0.0

    clock.advance(1_100)
    terminal = client.read_status()
    assert terminal.run_state is RunState.READY
    assert terminal.run_status is expected_status
    events = client.read_events(terminal.event_count)
    assert 1 <= len(events) < 360
    assert [event.travel_angle_deg for event in events] == list(range(1, len(events) + 1))


def test_heartbeat_loss_aborts_smoothly_and_reconnect_never_replays_start() -> None:
    clock = FakeMonotonicClock()
    client = commissioned_client(clock)
    client.send_start(Mode.AUTO, Direction.CW, 5)
    original_start_seq = client.start_command_seq

    clock.advance(1_001)
    overdue = client.read_status()
    assert overdue.run_state is RunState.STOPPING
    assert overdue.run_status is RunStatus.COMMUNICATION_ABORTED
    assert overdue.actual_velocity_deg_s > 0.0

    client.close()
    clock.advance(1_000)
    client.connect()
    terminal = client.read_status()
    assert terminal.run_state is RunState.READY
    assert terminal.run_status is RunStatus.COMMUNICATION_ABORTED
    assert terminal.actual_velocity_deg_s == 0.0
    assert client.start_command_seq == original_start_seq
    assert terminal.start_ack_seq == original_start_seq
    assert terminal.status_flags & STATUS_BUFFER_READY


def test_raw_u16_command_sequences_and_u32_plc_tick_wrap() -> None:
    clock = FakeMonotonicClock()
    client = commissioned_client(clock, initial_plc_tick_ms=0xFFFF_FFF0)

    for _ in range(0x1_0000):
        last_stop_seq = client.send_stop()
    assert last_stop_seq == 0

    clock.advance(32)
    status = client.read_status()
    assert status.stop_ack_seq == 0
    assert status.plc_tick_ms == 0x10


def test_status_reads_do_not_fake_heartbeat_health() -> None:
    clock = FakeMonotonicClock()
    client = commissioned_client(clock)
    client.send_start(Mode.AUTO, Direction.CW, 1)

    for _ in range(11):
        clock.advance(100)
        status = client.read_status()

    assert status.run_status is RunStatus.COMMUNICATION_ABORTED
