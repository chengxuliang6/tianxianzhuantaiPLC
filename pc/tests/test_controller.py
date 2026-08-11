from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from threading import Event, Thread, get_ident

import pytest

from turntable_control.controller import (
    CommandRejected,
    ControllerSnapshot,
    ControllerStopped,
    TurntableController,
)
from turntable_control.domain import Direction, Mode, RunState, RunStatus
from turntable_control.modbus_client import EventRecord, StartNotIssued, StartOutcomeUnknown, StatusSnapshot
from turntable_control.time_sync import ClockSynchronizer


def ready_status(**changes: object) -> StatusSnapshot:
    base = StatusSnapshot(
        run_state=RunState.READY,
        status_flags=0x0003,
        fault_code=0,
        actual_position_deg=0.0,
        target_position_deg=0.0,
        actual_velocity_deg_s=0.0,
        heartbeat_echo=0,
        start_ack_seq=0,
        stop_ack_seq=0,
        set_zero_ack_seq=0,
        reset_fault_ack_seq=0,
        power_ack_seq=0,
        buffer_acked_seq=0,
        event_count=0,
        event_generation=0,
        run_status=RunStatus.IDLE,
        run_start_plc_ms=0,
        protocol_version=1,
        word_order_probe=0x12345678,
        time_sync_request_seq=0,
        plc_tick_ms=100,
        time_sync_response_seq=0,
    )
    return replace(base, **changes)


class FakeClient:
    def __init__(self) -> None:
        self.status = ready_status()
        self.calls: list[tuple[object, ...]] = []
        self.start_seq = 0
        self.stop_seq = 0
        self.sync_seq = 0
        self.fail_next: dict[str, Exception] = {}
        self.events: list[EventRecord] = []
        self.io_threads: list[int] = []
        self.call_event = Event()

    def _fail(self, name: str) -> None:
        self.io_threads.append(get_ident())
        self.call_event.set()
        error = self.fail_next.pop(name, None)
        if error is not None:
            raise error

    def connect(self) -> None:
        self.calls.append(("connect",))
        self._fail("connect")

    def close(self) -> None:
        self.calls.append(("close",))
        self._fail("close")

    def read_status(self) -> StatusSnapshot:
        self.calls.append(("read_status",))
        self._fail("read_status")
        return self.status

    def request_time_sync(self) -> int:
        self.sync_seq = (self.sync_seq + 1) & 0xFFFF
        self.calls.append(("request_time_sync", self.sync_seq))
        self._fail("request_time_sync")
        return self.sync_seq

    def send_start(self, mode: Mode, direction: Direction, speed_index: int) -> int:
        self.start_seq = (self.start_seq + 1) & 0xFFFF
        self.calls.append(("send_start", mode, direction, speed_index))
        self._fail("send_start")
        return self.start_seq

    def send_stop(self) -> int:
        self.stop_seq = (self.stop_seq + 1) & 0xFFFF
        self.calls.append(("send_stop",))
        self._fail("send_stop")
        return self.stop_seq

    def set_zero(self) -> int:
        self.calls.append(("set_zero",))
        self._fail("set_zero")
        return 1

    def reset_alarm(self) -> int:
        self.calls.append(("reset_alarm",))
        self._fail("reset_alarm")
        return 1

    def toggle_power(self) -> int:
        self.calls.append(("toggle_power",))
        self._fail("toggle_power")
        return 1

    def write_heartbeat(self, value: int) -> None:
        self.calls.append(("write_heartbeat", value))
        self._fail("write_heartbeat")

    def read_events(self, count: int) -> list[EventRecord]:
        self.calls.append(("read_events", count))
        self._fail("read_events")
        return self.events[:count]

    def acknowledge_buffer(self) -> int:
        self.calls.append(("acknowledge_buffer",))
        self._fail("acknowledge_buffer")
        return 1


class FakeStore:
    def __init__(self, root: Path | None = None, calls: list[tuple[object, ...]] | None = None) -> None:
        self.root = root or Path("unused")
        self.calls = calls
        self.exports: list[object] = []
        self.failures: list[Exception] = []
        self.io_threads: list[int] = []

    def save_run(self, run: object, synchronizer: ClockSynchronizer) -> Path:
        self.io_threads.append(get_ident())
        if self.calls is not None:
            self.calls.append(("save_run",))
        if self.failures:
            raise self.failures.pop(0)
        self.exports.append(run)
        metadata = getattr(run, "metadata")
        return self.root / f"{metadata.test_id}.csv"


class FakeClock:
    def __init__(self, epoch_ms: int = 1_000, monotonic_ms: int = 0) -> None:
        self.epoch = epoch_ms
        self.monotonic = monotonic_ms

    def advance(self, milliseconds: int) -> None:
        self.epoch += milliseconds
        self.monotonic += milliseconds


def make_controller(
    client: FakeClient,
    *,
    clock: FakeClock | None = None,
    sync: ClockSynchronizer | None = None,
    store: FakeStore | None = None,
) -> TurntableController:
    clock = clock or FakeClock()
    return TurntableController(
        client,
        store or FakeStore(),
        sync or ClockSynchronizer(),
        epoch_ms=lambda: clock.epoch,
        monotonic_ms=lambda: clock.monotonic,
    )


def connect_controller(controller: TurntableController) -> None:
    controller.connect()
    controller.process_once()


def test_snapshot_is_immutable_and_public_api_is_present() -> None:
    client = FakeClient()
    controller = make_controller(client)

    assert isinstance(controller.snapshot, ControllerSnapshot)
    with pytest.raises(FrozenInstanceError):
        controller.snapshot.connected = True  # type: ignore[misc]
    for name in (
        "connect", "disconnect", "start", "stop", "set_zero", "reset_alarm",
        "toggle_power", "retry_download", "process_once", "start_background", "shutdown",
        "on_snapshot", "on_error", "on_run_saved",
    ):
        assert callable(getattr(controller, name))


def test_public_connect_only_enqueues_until_process_once() -> None:
    client = FakeClient()
    controller = make_controller(client)

    controller.connect()
    assert client.calls == []

    controller.process_once()
    assert [call[0] for call in client.calls] == ["connect", "read_status", "request_time_sync"]
    assert controller.snapshot.connected
    assert controller.snapshot.status == client.status


def test_public_start_only_enqueues_and_pump_maps_speed_index() -> None:
    client = FakeClient()
    controller = make_controller(client)
    connect_controller(controller)
    client.calls.clear()

    controller.start(Mode.AUTO, Direction.CW, 4.0)
    assert client.calls == []

    controller.process_once()
    assert [call[0] for call in client.calls] == ["read_status", "send_start", "request_time_sync"]
    assert client.calls[1] == ("send_start", Mode.AUTO, Direction.CW, 3)


def test_stop_has_priority_and_cancels_a_pending_start() -> None:
    client = FakeClient()
    controller = make_controller(client)
    connect_controller(controller)
    client.calls.clear()

    controller.start(Mode.AUTO, Direction.CW, 1.0)
    controller.stop()
    assert client.calls == []

    controller.process_once()
    assert client.calls == [("send_stop",)]


def test_only_one_start_can_be_queued_or_in_flight_before_plc_confirmation() -> None:
    client = FakeClient()
    controller = make_controller(client)
    connect_controller(controller)

    controller.start(Mode.AUTO, Direction.CW, 1.0)
    with pytest.raises(CommandRejected, match="启动命令正在等待PLC确认"):
        controller.start(Mode.MANUAL, Direction.CCW, 5.0)
    controller.process_once()
    with pytest.raises(CommandRejected, match="启动命令正在等待PLC确认"):
        controller.start(Mode.MANUAL, Direction.CCW, 5.0)

    assert [call[0] for call in client.calls].count("send_start") == 1
    assert ("send_start", Mode.MANUAL, Direction.CCW, 4) not in client.calls


def test_exact_plc_start_confirmation_releases_pending_guard() -> None:
    client = FakeClient()
    clock = FakeClock()
    controller = make_controller(client, clock=clock)
    connect_controller(controller)
    controller.start(Mode.AUTO, Direction.CW, 1.0)
    controller.process_once()
    client.status = ready_status(
        run_state=RunState.AUTO_RUNNING,
        run_status=RunStatus.RUNNING,
        start_ack_seq=1,
        run_start_plc_ms=123,
    )
    clock.advance(100)
    controller.process_once()
    client.status = ready_status()
    clock.advance(100)
    controller.process_once()

    controller.start(Mode.MANUAL, Direction.CCW, 5.0)
    controller.process_once()

    assert ("send_start", Mode.MANUAL, Direction.CCW, 4) in client.calls


def test_stop_clears_an_in_flight_start_guard_without_replaying_it() -> None:
    client = FakeClient()
    controller = make_controller(client)
    connect_controller(controller)
    controller.start(Mode.AUTO, Direction.CW, 1.0)
    controller.process_once()

    controller.stop()
    controller.process_once()
    client.status = ready_status()
    controller.start(Mode.MANUAL, Direction.CCW, 5.0)
    controller.process_once()

    assert [call[0] for call in client.calls].count("send_start") == 2
    assert [call[0] for call in client.calls].count("send_stop") == 1


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (ready_status(status_flags=0x0002), "未设零"),
        (ready_status(status_flags=0x0001), "伺服未就绪"),
        (ready_status(status_flags=0x000B), "上次数据尚未保存"),
        (ready_status(run_state=RunState.STOPPING), "转台当前不可启动"),
    ],
)
def test_start_interlocks_use_required_chinese_errors(status: StatusSnapshot, expected: str) -> None:
    client = FakeClient()
    client.status = status
    controller = make_controller(client)
    connect_controller(controller)

    with pytest.raises(CommandRejected, match=expected):
        controller.start(Mode.AUTO, Direction.CW, 1.0)


def test_start_rejects_invalid_types_speeds_and_direction_space() -> None:
    client = FakeClient()
    controller = make_controller(client)
    connect_controller(controller)

    for args in ((1, Direction.CW, 1.0), (Mode.AUTO, 1, 1.0), (Mode.AUTO, Direction.CW, 3.0)):
        with pytest.raises(CommandRejected):
            controller.start(*args)  # type: ignore[arg-type]

    client.status = ready_status(actual_position_deg=100.0)
    controller.process_once()  # scheduled polling is not due; replace via reconnect
    controller.disconnect()
    controller.process_once()
    controller.connect()
    controller.process_once()
    with pytest.raises(CommandRejected, match="该方向空间不足，请反向运行"):
        controller.start(Mode.AUTO, Direction.CW, 1.0)


def test_fresh_worker_recheck_rejects_stale_start_without_killing_pump() -> None:
    client = FakeClient()
    controller = make_controller(client)
    connect_controller(controller)
    errors: list[str] = []
    controller.on_error(errors.append)
    controller.start(Mode.AUTO, Direction.CW, 1.0)
    client.status = ready_status(status_flags=0x0002)
    client.calls.clear()

    controller.process_once()

    assert [call[0] for call in client.calls] == ["read_status"]
    assert controller.snapshot.status == client.status
    assert controller.snapshot.last_error == "未设零"
    assert errors == ["未设零"]


def test_fresh_start_recheck_retains_a_new_terminal_buffer_without_sending_start() -> None:
    client = FakeClient()
    controller = make_controller(client)
    connect_controller(controller)
    controller.start(Mode.AUTO, Direction.CW, 1.0)
    client.status = ready_status(
        status_flags=0x000B,
        start_ack_seq=42,
        event_count=1,
        event_generation=7,
        run_status=RunStatus.AUTOMATIC_ABORTED,
        run_start_plc_ms=100,
    )
    client.calls.clear()

    controller.process_once()

    assert [call[0] for call in client.calls] == ["read_status"]
    assert controller.snapshot.status == client.status
    assert controller.snapshot.download_pending
    assert "send_start" not in [call[0] for call in client.calls]
    assert "acknowledge_buffer" not in [call[0] for call in client.calls]


def test_disconnect_clears_pending_commands_and_reconnect_never_replays_start() -> None:
    client = FakeClient()
    controller = make_controller(client)
    connect_controller(controller)
    controller.start(Mode.AUTO, Direction.CW, 1.0)
    controller.toggle_power()
    controller.disconnect()
    client.calls.clear()

    controller.process_once()
    controller.connect()
    controller.process_once()

    names = [call[0] for call in client.calls]
    assert names[:4] == ["close", "connect", "read_status", "request_time_sync"]
    assert "send_start" not in names
    assert "toggle_power" not in names
    controller.start(Mode.MANUAL, Direction.CCW, 5.0)
    controller.process_once()
    assert ("send_start", Mode.MANUAL, Direction.CCW, 4) in client.calls


def test_disconnect_request_rejects_commands_queued_before_worker_closes() -> None:
    client = FakeClient()
    controller = make_controller(client)
    connect_controller(controller)

    controller.disconnect()
    with pytest.raises(CommandRejected, match="PLC未连接"):
        controller.start(Mode.AUTO, Direction.CW, 1.0)
    with pytest.raises(CommandRejected, match="PLC未连接"):
        controller.toggle_power()
    with pytest.raises(CommandRejected, match="PLC未连接"):
        controller.stop()

    controller.process_once()
    assert not controller.snapshot.connected


def test_normal_command_queue_is_bounded_at_32() -> None:
    client = FakeClient()
    controller = make_controller(client)
    connect_controller(controller)

    for _ in range(32):
        controller.toggle_power()
    with pytest.raises(CommandRejected, match="命令队列已满"):
        controller.toggle_power()


def test_communication_failure_disconnects_immediately_and_clears_pending_start() -> None:
    from turntable_control.modbus_client import CommunicationError

    client = FakeClient()
    controller = make_controller(client)
    connect_controller(controller)
    controller.start(Mode.AUTO, Direction.CW, 1.0)
    client.fail_next["read_status"] = CommunicationError("offline")

    controller.process_once()

    assert not controller.snapshot.connected
    assert "offline" in (controller.snapshot.last_error or "")
    assert [call[0] for call in client.calls].count("send_start") == 0
    controller.connect()
    controller.process_once()
    assert [call[0] for call in client.calls].count("send_start") == 0


def test_definite_pre_start_write_failure_clears_pending_guard() -> None:
    client = FakeClient()
    controller = make_controller(client)
    connect_controller(controller)
    client.fail_next["send_start"] = StartNotIssued("parameter write failed", 1)
    controller.start(Mode.AUTO, Direction.CW, 1.0)

    controller.process_once()
    assert not controller.snapshot.connected

    controller.connect()
    controller.process_once()
    controller.start(Mode.MANUAL, Direction.CCW, 5.0)
    controller.process_once()
    assert [call[0] for call in client.calls].count("send_start") == 2


def test_unknown_start_outcome_never_claims_saves_or_acks_a_terminal_buffer(tmp_path: Path) -> None:
    from turntable_control.modbus_client import CommunicationError

    client = FakeClient()
    clock = FakeClock()
    sync = ClockSynchronizer()
    sync.add_sample(1_000, 100, 1_000)
    store = FakeStore(tmp_path, client.calls)
    controller = make_controller(client, clock=clock, sync=sync, store=store)
    connect_controller(controller)
    client.fail_next["send_start"] = CommunicationError("start outcome has no sequence")
    controller.start(Mode.AUTO, Direction.CW, 1.0)
    controller.process_once()
    assert not controller.snapshot.connected

    client.events = [EventRecord(1, 1, 1.0, 10)]
    client.status = ready_status(
        status_flags=0x000B,
        start_ack_seq=1,
        event_count=1,
        event_generation=33,
        run_status=RunStatus.AUTOMATIC_ABORTED,
        run_start_plc_ms=100,
    )
    controller.connect()
    controller.process_once()

    names = [call[0] for call in client.calls]
    assert "read_events" not in names
    assert "save_run" not in names
    assert "acknowledge_buffer" not in names
    assert controller.snapshot.download_pending
    assert "\u4eba\u5de5\u5bf9\u8d26" in (controller.snapshot.last_error or "")
    assert "启动写入结果未知" in (controller.snapshot.last_error or "")


def test_exact_unknown_start_reconciles_terminal_buffer_without_replaying_motion(tmp_path: Path) -> None:
    client = FakeClient()
    clock = FakeClock()
    sync = ClockSynchronizer()
    sync.add_sample(1_000, 100, 1_000)
    store = FakeStore(tmp_path, client.calls)
    controller = make_controller(client, clock=clock, sync=sync, store=store)
    connect_controller(controller)
    client.fail_next["send_start"] = StartOutcomeUnknown("start response lost", 1)
    controller.start(Mode.AUTO, Direction.CW, 1.0)
    controller.process_once()
    client.events = [EventRecord(1, 1, 1.0, 10)]
    client.status = ready_status(
        status_flags=0x000B,
        start_ack_seq=1,
        event_count=1,
        event_generation=34,
        run_status=RunStatus.AUTOMATIC_ABORTED,
        run_start_plc_ms=100,
    )

    controller.connect()
    controller.process_once()

    names = [call[0] for call in client.calls]
    assert names.count("send_start") == 1
    assert names.count("save_run") == 1
    assert names.count("acknowledge_buffer") == 1
    assert not controller.snapshot.download_pending


@pytest.mark.parametrize("observed_ack", [0, 1])
def test_exact_unknown_start_clears_when_plc_is_ready_idle_without_buffer(
    observed_ack: int,
) -> None:
    client = FakeClient()
    controller = make_controller(client)
    connect_controller(controller)
    client.fail_next["send_start"] = StartOutcomeUnknown("start response lost", 1)
    controller.start(Mode.AUTO, Direction.CW, 1.0)
    controller.process_once()
    client.status = ready_status(start_ack_seq=observed_ack, run_status=RunStatus.IDLE)

    controller.connect()
    controller.process_once()

    assert [call[0] for call in client.calls].count("send_start") == 1
    controller.start(Mode.MANUAL, Direction.CCW, 5.0)
    controller.process_once()
    assert [call[0] for call in client.calls].count("send_start") == 2


def test_exact_unknown_start_conflict_remains_blocked_for_manual_reconciliation() -> None:
    client = FakeClient()
    controller = make_controller(client)
    connect_controller(controller)
    client.fail_next["send_start"] = StartOutcomeUnknown("start response lost", 1)
    controller.start(Mode.AUTO, Direction.CW, 1.0)
    controller.process_once()
    client.status = ready_status(
        status_flags=0x000B,
        start_ack_seq=2,
        event_count=1,
        event_generation=35,
        run_status=RunStatus.AUTOMATIC_ABORTED,
        run_start_plc_ms=100,
    )

    controller.connect()
    controller.process_once()

    with pytest.raises(CommandRejected):
        controller.start(Mode.MANUAL, Direction.CCW, 5.0)
    assert controller.snapshot.download_pending
    assert [call[0] for call in client.calls].count("send_start") == 1
    assert "\u4eba\u5de5\u5bf9\u8d26" in (controller.snapshot.last_error or "")


def test_exact_unknown_start_rejects_torn_terminal_and_running_state(tmp_path: Path) -> None:
    client = FakeClient()
    clock = FakeClock()
    sync = ClockSynchronizer()
    sync.add_sample(1_000, 100, 1_000)
    store = FakeStore(tmp_path, client.calls)
    controller = make_controller(client, clock=clock, sync=sync, store=store)
    connect_controller(controller)
    client.fail_next["send_start"] = StartOutcomeUnknown("start response lost", 1)
    controller.start(Mode.AUTO, Direction.CW, 1.0)
    controller.process_once()
    client.events = [EventRecord(1, 1, 1.0, 10)]
    client.status = ready_status(
        run_state=RunState.AUTO_RUNNING,
        status_flags=0x000B,
        start_ack_seq=1,
        event_count=1,
        event_generation=36,
        run_status=RunStatus.AUTOMATIC_ABORTED,
        run_start_plc_ms=100,
    )

    controller.connect()
    controller.process_once()

    names = [call[0] for call in client.calls]
    assert names.count("send_start") == 1
    assert "save_run" not in names
    assert "acknowledge_buffer" not in names
    assert controller.snapshot.download_pending
    assert "\u4eba\u5de5\u5bf9\u8d26" in (controller.snapshot.last_error or "")


def test_scheduler_polls_every_100_ms_and_heartbeats_every_250_ms() -> None:
    client = FakeClient()
    clock = FakeClock()
    controller = make_controller(client, clock=clock)
    connect_controller(controller)
    client.calls.clear()

    clock.advance(99)
    controller.process_once()
    assert client.calls == []
    clock.advance(1)
    controller.process_once()
    assert client.calls == [("read_status",)]
    clock.advance(100)
    controller.process_once()
    assert [call[0] for call in client.calls] == ["read_status", "read_status"]
    clock.advance(49)
    controller.process_once()
    assert [call[0] for call in client.calls] == ["read_status", "read_status"]
    clock.advance(1)
    controller.process_once()
    assert client.calls[-1] == ("write_heartbeat", 1)


def test_heartbeat_starts_from_echo_and_wraps_as_raw_u16() -> None:
    client = FakeClient()
    client.status = ready_status(heartbeat_echo=0xFFFF)
    clock = FakeClock()
    controller = make_controller(client, clock=clock)
    connect_controller(controller)
    client.calls.clear()

    clock.advance(250)
    controller.process_once()

    assert ("write_heartbeat", 0) in client.calls


def test_time_sync_only_accepts_a_matching_response_sequence() -> None:
    client = FakeClient()
    clock = FakeClock(epoch_ms=10_000)
    sync = ClockSynchronizer()
    controller = make_controller(client, clock=clock, sync=sync)
    connect_controller(controller)
    assert sync.sample_count == 0

    client.status = ready_status(time_sync_response_seq=0, plc_tick_ms=500)
    clock.advance(100)
    controller.process_once()
    assert sync.sample_count == 0

    client.status = ready_status(time_sync_response_seq=1, plc_tick_ms=600)
    clock.advance(100)
    controller.process_once()

    assert sync.sample_count == 1
    assert sync.best_sample is not None
    assert sync.best_sample.pc_send_ms == 10_000
    assert sync.best_sample.pc_recv_ms == 10_200
    assert sync.best_sample.plc_ms == 600


def test_invalid_time_sync_sample_is_consumed_reported_and_does_not_stop_heartbeat() -> None:
    client = FakeClient()
    clock = FakeClock(epoch_ms=1_000)
    sync = ClockSynchronizer()
    controller = make_controller(client, clock=clock, sync=sync)
    errors: list[str] = []
    controller.on_error(errors.append)
    connect_controller(controller)
    client.calls.clear()
    client.status = ready_status(time_sync_response_seq=1, plc_tick_ms=500)
    clock.epoch = 999
    clock.monotonic = 100

    controller.process_once()

    assert sync.sample_count == 0
    assert controller.snapshot.connected
    assert errors and "clock synchronization" in errors[0]
    client.calls.clear()
    clock.monotonic = 250
    controller.process_once()
    assert ("write_heartbeat", 1) in client.calls
    assert len(errors) == 1


def test_run_start_tick_change_is_a_session_fingerprint_conflict(tmp_path: Path) -> None:
    client = FakeClient()
    clock = FakeClock()
    sync = ClockSynchronizer()
    sync.add_sample(1_000, 100, 1_000)
    store = FakeStore(tmp_path, client.calls)
    controller = make_controller(client, clock=clock, sync=sync, store=store)
    connect_controller(controller)
    controller.start(Mode.AUTO, Direction.CW, 1.0)
    controller.process_once()
    client.status = ready_status(
        run_state=RunState.AUTO_RUNNING,
        run_status=RunStatus.RUNNING,
        start_ack_seq=1,
        run_start_plc_ms=100,
    )
    clock.advance(100)
    controller.process_once()
    client.events = [EventRecord(1, 1, 1.0, 10)]
    client.status = ready_status(
        status_flags=0x000B,
        run_status=RunStatus.AUTOMATIC_ABORTED,
        start_ack_seq=1,
        event_count=1,
        event_generation=37,
        run_start_plc_ms=999,
    )
    clock.advance(100)

    controller.process_once()

    names = [call[0] for call in client.calls]
    assert "save_run" not in names
    assert "acknowledge_buffer" not in names
    assert controller.snapshot.download_pending
    assert "run-start tick" in (controller.snapshot.last_error or "")


def test_known_session_rejects_terminal_buffer_with_running_state(tmp_path: Path) -> None:
    client = FakeClient()
    clock = FakeClock()
    sync = ClockSynchronizer()
    sync.add_sample(1_000, 100, 1_000)
    store = FakeStore(tmp_path, client.calls)
    controller = make_controller(client, clock=clock, sync=sync, store=store)
    connect_controller(controller)
    controller.start(Mode.AUTO, Direction.CW, 1.0)
    controller.process_once()
    client.events = [EventRecord(1, 1, 1.0, 10)]
    client.status = ready_status(
        run_state=RunState.AUTO_RUNNING,
        status_flags=0x000B,
        run_status=RunStatus.AUTOMATIC_ABORTED,
        start_ack_seq=1,
        event_count=1,
        event_generation=38,
        run_start_plc_ms=100,
    )
    clock.advance(100)

    controller.process_once()

    names = [call[0] for call in client.calls]
    assert "save_run" not in names
    assert "acknowledge_buffer" not in names
    assert controller.snapshot.download_pending
    assert "state" in (controller.snapshot.last_error or "")


@pytest.mark.parametrize("terminal_status", [RunStatus.COMPLETED, RunStatus.AUTOMATIC_ABORTED])
def test_terminal_pipeline_uses_exact_run_start_and_orders_read_save_ack(
    tmp_path: Path, terminal_status: RunStatus
) -> None:
    client = FakeClient()
    clock = FakeClock(epoch_ms=20_000)
    sync = ClockSynchronizer()
    sync.add_sample(19_000, 0xFFFF_FF00, 19_000)
    store = FakeStore(tmp_path, client.calls)
    controller = make_controller(client, clock=clock, sync=sync, store=store)
    saved: list[Path] = []
    controller.on_run_saved(saved.append)
    connect_controller(controller)
    controller.start(Mode.AUTO, Direction.CW, 1.0)
    controller.process_once()
    event_count = 360 if terminal_status is RunStatus.COMPLETED else 2
    client.events = [EventRecord(index, index, float(index), index + 1) for index in range(1, event_count + 1)]
    client.status = ready_status(
        status_flags=0x000B,
        start_ack_seq=1,
        event_count=event_count,
        event_generation=7,
        run_status=terminal_status,
        run_start_plc_ms=0xFFFF_FFFE,
    )
    client.calls.clear()

    clock.advance(100)
    controller.process_once()

    names = [call[0] for call in client.calls]
    assert names.index("read_events") < names.index("save_run") < names.index("acknowledge_buffer")
    export = store.exports[0]
    assert export.metadata.run_start_plc_ms == 0xFFFF_FFFE
    assert export.metadata.run_status is terminal_status
    assert export.metadata.test_id.endswith("_g7")
    assert controller.snapshot.saved_csv == saved[0]
    assert not controller.snapshot.download_pending


def test_terminal_pipeline_rejects_a_travel_angle_gap_without_ack(tmp_path: Path) -> None:
    client = FakeClient()
    clock = FakeClock()
    sync = ClockSynchronizer()
    sync.add_sample(1_000, 100, 1_000)
    store = FakeStore(tmp_path, client.calls)
    controller = make_controller(client, clock=clock, sync=sync, store=store)
    connect_controller(controller)
    controller.start(Mode.MANUAL, Direction.CW, 1.0)
    controller.process_once()
    client.events = [EventRecord(1, 1, 1.0, 10), EventRecord(2, 3, 3.0, 20)]
    client.status = ready_status(
        status_flags=0x000B,
        start_ack_seq=1,
        event_count=2,
        event_generation=40,
        run_status=RunStatus.MANUAL_STOPPED,
        run_start_plc_ms=100,
    )

    clock.advance(100)
    controller.process_once()

    assert not store.exports
    assert "acknowledge_buffer" not in [call[0] for call in client.calls]
    assert controller.snapshot.download_pending
    assert "行程角度" in (controller.snapshot.last_error or "")


def test_auto_completed_requires_exactly_360_events_before_save_or_ack(tmp_path: Path) -> None:
    client = FakeClient()
    clock = FakeClock()
    sync = ClockSynchronizer()
    sync.add_sample(1_000, 100, 1_000)
    store = FakeStore(tmp_path, client.calls)
    controller = make_controller(client, clock=clock, sync=sync, store=store)
    connect_controller(controller)
    controller.start(Mode.AUTO, Direction.CW, 1.0)
    controller.process_once()
    client.events = [EventRecord(index, index, float(index), index) for index in range(1, 360)]
    client.status = ready_status(
        status_flags=0x000B,
        start_ack_seq=1,
        event_count=359,
        event_generation=41,
        run_status=RunStatus.COMPLETED,
        run_start_plc_ms=100,
    )

    clock.advance(100)
    controller.process_once()

    assert not store.exports
    assert "acknowledge_buffer" not in [call[0] for call in client.calls]
    assert controller.snapshot.download_pending
    assert "360" in (controller.snapshot.last_error or "")


def test_save_failure_retains_buffer_and_only_explicit_retry_retries(tmp_path: Path) -> None:
    from turntable_control.csv_store import CsvSaveError

    client = FakeClient()
    clock = FakeClock()
    sync = ClockSynchronizer()
    sync.add_sample(1_000, 100, 1_000)
    store = FakeStore(tmp_path, client.calls)
    store.failures.append(CsvSaveError("disk full"))
    controller = make_controller(client, clock=clock, sync=sync, store=store)
    connect_controller(controller)
    controller.start(Mode.MANUAL, Direction.CW, 1.0)
    controller.process_once()
    client.events = [EventRecord(1, 1, 1.0, 10)]
    client.status = ready_status(
        status_flags=0x000B,
        start_ack_seq=1,
        event_count=1,
        event_generation=8,
        run_status=RunStatus.MANUAL_STOPPED,
        run_start_plc_ms=100,
    )
    client.calls.clear()

    clock.advance(100)
    controller.process_once()
    assert controller.snapshot.download_pending
    assert "acknowledge_buffer" not in [call[0] for call in client.calls]
    assert [call[0] for call in client.calls].count("save_run") == 1

    clock.advance(100)
    controller.process_once()
    assert [call[0] for call in client.calls].count("save_run") == 1

    controller.retry_download()
    controller.process_once()
    assert [call[0] for call in client.calls].count("save_run") == 2
    assert [call[0] for call in client.calls].count("acknowledge_buffer") == 1
    assert not controller.snapshot.download_pending


def test_ack_uncertainty_reconnects_and_reuses_durable_file_without_resave(tmp_path: Path) -> None:
    from turntable_control.modbus_client import CommunicationError

    client = FakeClient()
    clock = FakeClock()
    sync = ClockSynchronizer()
    sync.add_sample(1_000, 100, 1_000)
    store = FakeStore(tmp_path, client.calls)
    controller = make_controller(client, clock=clock, sync=sync, store=store)
    connect_controller(controller)
    controller.start(Mode.AUTO, Direction.CW, 1.0)
    controller.process_once()
    client.events = [EventRecord(1, 1, 1.0, 10)]
    client.status = ready_status(
        status_flags=0x000B,
        start_ack_seq=1,
        event_count=1,
        event_generation=9,
        run_status=RunStatus.AUTOMATIC_ABORTED,
        run_start_plc_ms=100,
    )
    client.fail_next["acknowledge_buffer"] = CommunicationError("ack response lost")
    client.calls.clear()

    clock.advance(100)
    controller.process_once()
    durable = controller.snapshot.saved_csv
    assert durable is not None
    assert not controller.snapshot.connected
    assert controller.snapshot.download_pending
    assert [call[0] for call in client.calls].count("save_run") == 1

    controller.connect()
    controller.process_once()
    assert [call[0] for call in client.calls].count("save_run") == 1
    assert [call[0] for call in client.calls].count("acknowledge_buffer") == 2
    assert controller.snapshot.saved_csv == durable
    assert not controller.snapshot.download_pending


@pytest.mark.parametrize(
    "mismatch",
    [
        {"start_ack_seq": 77},
        {"event_generation": 10},
        {"event_count": 2},
        {"run_status": RunStatus.COMMUNICATION_ABORTED},
        {"run_start_plc_ms": 101},
    ],
    ids=("start-ack", "generation", "event-count", "terminal-status", "run-start-tick"),
)
def test_ack_recovery_refuses_any_buffer_fingerprint_mismatch(
    tmp_path: Path, mismatch: dict[str, object]
) -> None:
    from turntable_control.modbus_client import CommunicationError

    client = FakeClient()
    clock = FakeClock()
    sync = ClockSynchronizer()
    sync.add_sample(1_000, 100, 1_000)
    store = FakeStore(tmp_path, client.calls)
    controller = make_controller(client, clock=clock, sync=sync, store=store)
    connect_controller(controller)
    controller.start(Mode.AUTO, Direction.CW, 1.0)
    controller.process_once()
    client.events = [EventRecord(1, 1, 1.0, 10)]
    saved_status = ready_status(
        status_flags=0x000B,
        start_ack_seq=1,
        event_count=1,
        event_generation=9,
        run_status=RunStatus.AUTOMATIC_ABORTED,
        run_start_plc_ms=100,
    )
    client.status = saved_status
    client.fail_next["acknowledge_buffer"] = CommunicationError("ack response lost")
    clock.advance(100)
    controller.process_once()
    assert controller.snapshot.download_pending
    assert [call[0] for call in client.calls].count("save_run") == 1
    assert [call[0] for call in client.calls].count("acknowledge_buffer") == 1

    client.status = replace(saved_status, **mismatch)
    controller.connect()
    controller.process_once()

    assert [call[0] for call in client.calls].count("save_run") == 1
    assert [call[0] for call in client.calls].count("acknowledge_buffer") == 1
    assert controller.snapshot.download_pending
    assert "指纹不一致" in (controller.snapshot.last_error or "")


@pytest.mark.parametrize("evidence_field", ["active_test_id", "saved_csv"])
def test_ack_recovery_refuses_published_evidence_mismatch(
    tmp_path: Path, evidence_field: str
) -> None:
    from turntable_control.modbus_client import CommunicationError

    client = FakeClient()
    clock = FakeClock()
    sync = ClockSynchronizer()
    sync.add_sample(1_000, 100, 1_000)
    store = FakeStore(tmp_path, client.calls)
    controller = make_controller(client, clock=clock, sync=sync, store=store)
    connect_controller(controller)
    controller.start(Mode.AUTO, Direction.CW, 1.0)
    controller.process_once()
    client.events = [EventRecord(1, 1, 1.0, 10)]
    client.status = ready_status(
        status_flags=0x000B,
        start_ack_seq=1,
        event_count=1,
        event_generation=9,
        run_status=RunStatus.AUTOMATIC_ABORTED,
        run_start_plc_ms=100,
    )
    client.fail_next["acknowledge_buffer"] = CommunicationError("ack response lost")
    clock.advance(100)
    controller.process_once()
    if evidence_field == "active_test_id":
        controller._snapshot = replace(controller.snapshot, active_test_id="different-test")
    else:
        controller._snapshot = replace(controller.snapshot, saved_csv=tmp_path / "different.csv")

    controller.connect()
    controller.process_once()

    assert [call[0] for call in client.calls].count("acknowledge_buffer") == 1
    assert controller.snapshot.download_pending
    assert controller.snapshot.last_error is not None


def test_duplicate_generation_is_saved_and_acknowledged_at_most_once(tmp_path: Path) -> None:
    client = FakeClient()
    clock = FakeClock()
    sync = ClockSynchronizer()
    sync.add_sample(1_000, 100, 1_000)
    store = FakeStore(tmp_path, client.calls)
    controller = make_controller(client, clock=clock, sync=sync, store=store)
    connect_controller(controller)
    controller.start(Mode.AUTO, Direction.CW, 1.0)
    controller.process_once()
    client.events = [EventRecord(1, 1, 1.0, 10)]
    client.status = ready_status(
        status_flags=0x000B,
        start_ack_seq=1,
        event_count=1,
        event_generation=10,
        run_status=RunStatus.AUTOMATIC_ABORTED,
        run_start_plc_ms=100,
    )
    client.calls.clear()

    for _ in range(3):
        clock.advance(100)
        controller.process_once()

    assert [call[0] for call in client.calls].count("save_run") == 1
    assert [call[0] for call in client.calls].count("acknowledge_buffer") == 1


def test_event_read_failure_requires_reconnect_and_explicit_retry(tmp_path: Path) -> None:
    from turntable_control.modbus_client import CommunicationError

    client = FakeClient()
    clock = FakeClock()
    sync = ClockSynchronizer()
    sync.add_sample(1_000, 100, 1_000)
    store = FakeStore(tmp_path, client.calls)
    controller = make_controller(client, clock=clock, sync=sync, store=store)
    connect_controller(controller)
    controller.start(Mode.AUTO, Direction.CW, 1.0)
    controller.process_once()
    client.events = [EventRecord(1, 1, 1.0, 10)]
    client.status = ready_status(
        status_flags=0x000B,
        start_ack_seq=1,
        event_count=1,
        event_generation=11,
        run_status=RunStatus.AUTOMATIC_ABORTED,
        run_start_plc_ms=100,
    )
    client.fail_next["read_events"] = CommunicationError("event read lost")

    clock.advance(100)
    controller.process_once()
    assert not controller.snapshot.connected
    assert controller.snapshot.download_pending
    assert "acknowledge_buffer" not in [call[0] for call in client.calls]

    controller.connect()
    controller.process_once()
    assert [call[0] for call in client.calls].count("save_run") == 0
    controller.retry_download()
    controller.process_once()
    assert [call[0] for call in client.calls].count("save_run") == 1
    assert [call[0] for call in client.calls].count("acknowledge_buffer") == 1


def test_reconnect_marks_saved_ack_complete_when_plc_already_cleared_buffer(tmp_path: Path) -> None:
    from turntable_control.modbus_client import CommunicationError

    client = FakeClient()
    clock = FakeClock()
    sync = ClockSynchronizer()
    sync.add_sample(1_000, 100, 1_000)
    store = FakeStore(tmp_path, client.calls)
    controller = make_controller(client, clock=clock, sync=sync, store=store)
    connect_controller(controller)
    controller.start(Mode.AUTO, Direction.CW, 1.0)
    controller.process_once()
    client.events = [EventRecord(1, 1, 1.0, 10)]
    client.status = ready_status(
        status_flags=0x000B,
        start_ack_seq=1,
        event_count=1,
        event_generation=12,
        run_status=RunStatus.AUTOMATIC_ABORTED,
        run_start_plc_ms=100,
    )
    client.fail_next["acknowledge_buffer"] = CommunicationError("ack response lost")
    clock.advance(100)
    controller.process_once()
    assert controller.snapshot.download_pending

    client.status = ready_status(event_generation=12, run_status=RunStatus.AUTOMATIC_ABORTED)
    controller.connect()
    controller.process_once()

    assert [call[0] for call in client.calls].count("save_run") == 1
    assert [call[0] for call in client.calls].count("acknowledge_buffer") == 1
    assert not controller.snapshot.download_pending


def test_callback_exceptions_are_reported_and_do_not_corrupt_state() -> None:
    client = FakeClient()
    controller = make_controller(client)
    received: list[ControllerSnapshot] = []
    errors: list[str] = []

    def bad_callback(snapshot: ControllerSnapshot) -> None:
        raise RuntimeError("broken observer")

    unsubscribe = controller.on_snapshot(bad_callback)
    controller.on_snapshot(received.append)
    controller.on_error(errors.append)
    connect_controller(controller)

    assert controller.snapshot.connected
    assert received[-1].connected
    assert any("broken observer" in error for error in errors)
    unsubscribe()
    client.calls.clear()
    controller.toggle_power()
    controller.process_once()
    assert client.calls == [("toggle_power",)]


def test_background_is_idempotent_shutdown_closes_on_the_single_worker_thread() -> None:
    client = FakeClient()
    controller = make_controller(client)
    caller_thread = get_ident()
    connected = Event()
    controller.on_snapshot(lambda snapshot: connected.set() if snapshot.connected else None)
    controller.connect()

    controller.start_background()
    controller.start_background()
    assert connected.wait(1.0)
    client.call_event.clear()
    controller.toggle_power()
    assert client.call_event.wait(1.0)
    controller.shutdown(timeout=1.0)

    assert [call[0] for call in client.calls].count("connect") == 1
    assert [call[0] for call in client.calls].count("toggle_power") == 1
    assert [call[0] for call in client.calls].count("close") == 1
    assert len(set(client.io_threads)) == 1
    assert client.io_threads[0] != caller_thread
    with pytest.raises(Exception, match="已停止"):
        controller.connect()


def test_process_once_rejects_a_second_io_thread() -> None:
    client = FakeClient()
    controller = make_controller(client)
    connect_controller(controller)
    errors: list[Exception] = []

    thread = Thread(target=lambda: _capture_process_error(controller, errors))
    thread.start()
    thread.join()

    assert len(errors) == 1
    assert "同一控制线程" in str(errors[0])


def test_background_mode_is_rejected_after_deterministic_pump_claims_io_thread() -> None:
    client = FakeClient()
    controller = make_controller(client)
    connect_controller(controller)

    with pytest.raises(ControllerStopped):
        controller.start_background()

    controller.shutdown(timeout=1.0)
    assert len(set(client.io_threads)) == 1


def test_shutdown_in_deterministic_pump_mode_closes_through_the_pump() -> None:
    client = FakeClient()
    controller = make_controller(client)
    connect_controller(controller)
    client.calls.clear()

    controller.shutdown(timeout=1.0)

    assert client.calls == [("close",)]
    assert not controller.snapshot.connected


def test_deterministic_shutdown_reports_close_failure_and_publishes_disconnected() -> None:
    from turntable_control.modbus_client import CommunicationError

    client = FakeClient()
    controller = make_controller(client)
    connect_controller(controller)
    client.fail_next["close"] = CommunicationError("close failed")

    with pytest.raises(ControllerStopped, match="close failed"):
        controller.shutdown(timeout=1.0)

    assert not controller.snapshot.connected
    assert "close failed" in (controller.snapshot.last_error or "")


def test_background_shutdown_reports_close_failure_and_publishes_disconnected() -> None:
    from turntable_control.modbus_client import CommunicationError

    client = FakeClient()
    controller = make_controller(client)
    connected = Event()
    controller.on_snapshot(lambda snapshot: connected.set() if snapshot.connected else None)
    controller.connect()
    controller.start_background()
    assert connected.wait(1.0)
    client.fail_next["close"] = CommunicationError("close failed")

    with pytest.raises(ControllerStopped, match="close failed"):
        controller.shutdown(timeout=1.0)

    assert not controller.snapshot.connected
    assert "close failed" in (controller.snapshot.last_error or "")


def test_disconnect_discards_an_unanswered_time_sync_request() -> None:
    client = FakeClient()
    sync = ClockSynchronizer()
    controller = make_controller(client, sync=sync)
    connect_controller(controller)
    controller.disconnect()
    controller.process_once()
    client.status = ready_status(time_sync_response_seq=1, plc_tick_ms=500)

    controller.connect()
    controller.process_once()

    assert sync.sample_count == 0


def test_a_new_run_can_reuse_a_wrapped_generation_without_reusing_old_evidence(tmp_path: Path) -> None:
    client = FakeClient()
    clock = FakeClock()
    sync = ClockSynchronizer()
    sync.add_sample(1_000, 100, 1_000)
    store = FakeStore(tmp_path, client.calls)
    controller = make_controller(client, clock=clock, sync=sync, store=store)
    connect_controller(controller)
    client.events = [EventRecord(1, 1, 1.0, 10)]

    for run_number in range(2):
        client.status = ready_status()
        if run_number:
            clock.advance(100)
            controller.process_once()
            clock.advance(1)
        controller.start(Mode.AUTO, Direction.CW, 1.0)
        controller.process_once()
        client.status = ready_status(
            status_flags=0x000B,
            start_ack_seq=run_number + 1,
            event_count=1,
            event_generation=0,
            run_status=RunStatus.AUTOMATIC_ABORTED,
            run_start_plc_ms=100 + run_number,
        )
        clock.advance(100)
        controller.process_once()

    assert len(store.exports) == 2
    assert store.exports[0].metadata.test_id != store.exports[1].metadata.test_id
    assert [call[0] for call in client.calls].count("acknowledge_buffer") == 2


def _capture_process_error(controller: TurntableController, errors: list[Exception]) -> None:
    try:
        controller.process_once()
    except Exception as error:
        errors.append(error)
