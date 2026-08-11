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
        self.observed_start_command_seq = 0
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

    @property
    def start_command_seq(self) -> int:
        return self.observed_start_command_seq

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
        self.observed_start_command_seq = self.start_seq
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


def test_queued_start_stop_barrier_blocks_replacement_until_stop_ack() -> None:
    client = FakeClient()
    clock = FakeClock()
    controller = make_controller(client, clock=clock)
    connect_controller(controller)
    controller.start(Mode.AUTO, Direction.CW, 1.0)
    controller.stop()
    controller.process_once()

    with pytest.raises(CommandRejected):
        controller.start(Mode.MANUAL, Direction.CCW, 5.0)
    client.status = ready_status(stop_ack_seq=1)
    clock.advance(100)
    controller.process_once()
    controller.start(Mode.MANUAL, Direction.CCW, 5.0)
    controller.process_once()

    assert [call[0] for call in client.calls].count("send_stop") == 1
    assert [call[0] for call in client.calls].count("send_start") == 1


def test_stop_only_barrier_clears_for_ready_retained_terminal_status() -> None:
    client = FakeClient()
    clock = FakeClock()
    client.status = ready_status(run_status=RunStatus.AUTOMATIC_ABORTED)
    controller = make_controller(client, clock=clock)
    connect_controller(controller)

    controller.stop()
    controller.process_once()
    with pytest.raises(CommandRejected):
        controller.start(Mode.MANUAL, Direction.CCW, 5.0)
    client.status = ready_status(
        run_status=RunStatus.AUTOMATIC_ABORTED,
        stop_ack_seq=1,
    )
    clock.advance(100)
    controller.process_once()
    controller.start(Mode.MANUAL, Direction.CCW, 5.0)
    controller.process_once()

    assert [call[0] for call in client.calls].count("send_stop") == 1
    assert [call[0] for call in client.calls].count("send_start") == 1


def test_disconnect_clears_unsent_queued_start_and_stop_barrier() -> None:
    client = FakeClient()
    controller = make_controller(client)
    connect_controller(controller)
    controller.start(Mode.AUTO, Direction.CW, 1.0)
    controller.stop()
    controller.disconnect()
    controller.process_once()
    client.status = ready_status()
    controller.connect()
    controller.process_once()

    controller.start(Mode.MANUAL, Direction.CCW, 5.0)
    controller.process_once()

    names = [call[0] for call in client.calls]
    assert "send_stop" not in names
    assert names.count("send_start") == 1


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


def test_incoherent_exact_start_ack_does_not_release_issued_guard() -> None:
    client = FakeClient()
    clock = FakeClock()
    controller = make_controller(client, clock=clock)
    connect_controller(controller)
    controller.start(Mode.AUTO, Direction.CW, 1.0)
    controller.process_once()
    client.status = ready_status(
        run_state=RunState.MANUAL_RUNNING,
        run_status=RunStatus.RUNNING,
        start_ack_seq=1,
        run_start_plc_ms=123,
    )
    clock.advance(100)

    controller.process_once()

    assert controller._issued_start_seq == 1
    assert "state" in (controller.snapshot.last_error or "")


def test_stop_keeps_an_issued_start_cancelling_until_both_acks_and_stopped_state() -> None:
    client = FakeClient()
    clock = FakeClock()
    controller = make_controller(client, clock=clock)
    connect_controller(controller)
    controller.start(Mode.AUTO, Direction.CW, 1.0)
    controller.process_once()

    controller.stop()
    controller.process_once()
    with pytest.raises(CommandRejected):
        controller.start(Mode.MANUAL, Direction.CCW, 5.0)
    client.status = ready_status(start_ack_seq=1, stop_ack_seq=1)
    clock.advance(100)
    controller.process_once()
    controller.start(Mode.MANUAL, Direction.CCW, 5.0)
    controller.process_once()

    assert [call[0] for call in client.calls].count("send_start") == 2
    assert [call[0] for call in client.calls].count("send_stop") == 1


@pytest.mark.parametrize(
    "status",
    [
        ready_status(start_ack_seq=0, stop_ack_seq=1),
        ready_status(start_ack_seq=1, stop_ack_seq=0),
        ready_status(
            run_state=RunState.STOPPING,
            run_status=RunStatus.AUTOMATIC_ABORTED,
            start_ack_seq=1,
            stop_ack_seq=1,
        ),
    ],
    ids=("missing-start-ack", "missing-stop-ack", "not-stopped"),
)
def test_issued_start_cancellation_blocks_replacement_until_full_barrier(
    status: StatusSnapshot,
) -> None:
    client = FakeClient()
    clock = FakeClock()
    controller = make_controller(client, clock=clock)
    connect_controller(controller)
    controller.start(Mode.AUTO, Direction.CW, 1.0)
    controller.process_once()
    controller.stop()
    controller.process_once()
    client.status = status
    clock.advance(100)
    controller.process_once()

    with pytest.raises(CommandRejected):
        controller.start(Mode.MANUAL, Direction.CCW, 5.0)

    assert [call[0] for call in client.calls].count("send_start") == 1


def test_confirmed_running_stop_clears_barrier_after_terminal_save_and_ack(tmp_path: Path) -> None:
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
    controller.stop()
    controller.process_once()
    client.events = [EventRecord(1, 1, 1.0, 10)]
    client.status = ready_status(
        status_flags=0x000B,
        start_ack_seq=1,
        stop_ack_seq=1,
        event_count=1,
        event_generation=40,
        run_status=RunStatus.AUTOMATIC_ABORTED,
        run_start_plc_ms=100,
    )
    clock.advance(100)
    controller.process_once()
    client.status = ready_status(
        start_ack_seq=1,
        stop_ack_seq=1,
        run_status=RunStatus.AUTOMATIC_ABORTED,
        run_start_plc_ms=100,
    )
    clock.advance(100)
    controller.process_once()

    controller.start(Mode.MANUAL, Direction.CCW, 5.0)
    controller.process_once()

    assert [call[0] for call in client.calls].count("save_run") == 1
    assert [call[0] for call in client.calls].count("acknowledge_buffer") == 1
    assert [call[0] for call in client.calls].count("send_start") == 2


def test_confirmed_running_stop_rejects_ready_idle_without_terminal_buffer() -> None:
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
    controller.stop()
    controller.process_once()
    client.status = ready_status(start_ack_seq=1, stop_ack_seq=1)
    clock.advance(100)
    controller.process_once()

    with pytest.raises(CommandRejected):
        controller.start(Mode.MANUAL, Direction.CCW, 5.0)

    assert controller._run_session is not None
    assert controller._start_cancellation is not None
    assert "terminal" in (controller.snapshot.last_error or "")


def test_stop_during_running_status_publish_keeps_confirmed_session_identity() -> None:
    client = FakeClient()
    clock = FakeClock()
    controller = make_controller(client, clock=clock)
    connected = Event()
    issued = Event()
    publish_entered = Event()
    release_publish = Event()
    armed = False
    controller.on_snapshot(lambda snapshot: connected.set() if snapshot.connected else None)
    controller.on_snapshot(lambda snapshot: issued.set() if snapshot.active_test_id else None)
    original_publish = controller._publish

    def blocking_publish(snapshot: ControllerSnapshot) -> None:
        if armed and snapshot.status is client.status and snapshot.status.run_status is RunStatus.RUNNING:
            publish_entered.set()
            assert release_publish.wait(1.0)
        original_publish(snapshot)

    controller._publish = blocking_publish  # type: ignore[method-assign]
    controller.connect()
    controller.start_background()
    assert connected.wait(1.0)
    controller.start(Mode.AUTO, Direction.CW, 1.0)
    controller._wake.set()
    assert issued.wait(1.0)
    client.status = ready_status(
        run_state=RunState.AUTO_RUNNING,
        run_status=RunStatus.RUNNING,
        start_ack_seq=1,
        run_start_plc_ms=123,
    )
    armed = True
    clock.advance(100)
    controller._wake.set()
    assert publish_entered.wait(1.0)

    try:
        controller.stop()
        assert controller._start_cancellation is not None
        assert controller._start_cancellation.start_seq == 1
        assert controller._start_cancellation.mode is Mode.AUTO
    finally:
        release_publish.set()
        controller.shutdown(timeout=1.0)


def test_stop_cannot_observe_half_applied_explicit_start_rejection() -> None:
    clear_entered = Event()
    release_clear = Event()

    class BlockingSessionClearController(TurntableController):
        block_session_clear = False

        def __setattr__(self, name: str, value: object) -> None:
            super().__setattr__(name, value)
            if name == "_run_session" and value is None and self.block_session_clear:
                self.block_session_clear = False
                clear_entered.set()
                assert release_clear.wait(1.0)

    client = FakeClient()
    clock = FakeClock()
    controller = BlockingSessionClearController(
        client,
        FakeStore(),
        ClockSynchronizer(),
        epoch_ms=lambda: clock.epoch,
        monotonic_ms=lambda: clock.monotonic,
    )
    connected = Event()
    issued = Event()
    controller.on_snapshot(lambda snapshot: connected.set() if snapshot.connected else None)
    controller.on_snapshot(lambda snapshot: issued.set() if snapshot.active_test_id else None)
    controller.connect()
    controller.start_background()
    assert connected.wait(1.0)
    controller.start(Mode.AUTO, Direction.CW, 1.0)
    controller._wake.set()
    assert issued.wait(1.0)
    client.status = ready_status(
        run_state=RunState.FAULT,
        run_status=RunStatus.FAULTED,
        fault_code=10,
        start_ack_seq=1,
    )
    controller.block_session_clear = True
    clock.advance(100)
    controller._wake.set()
    assert clear_entered.wait(1.0)
    stop_errors: list[Exception] = []
    stop_done = Event()

    def request_stop() -> None:
        try:
            controller.stop()
        except Exception as error:
            stop_errors.append(error)
        finally:
            stop_done.set()

    thread = Thread(target=request_stop)
    thread.start()
    try:
        assert not stop_done.wait(0.1)
    finally:
        release_clear.set()
        thread.join(1.0)
        controller.shutdown(timeout=1.0)

    assert stop_done.is_set()
    assert stop_errors == []


def test_repeated_stop_cannot_lose_barrier_during_old_ack_reconciliation() -> None:
    client = FakeClient()
    controller = make_controller(client)
    connect_controller(controller)
    controller.start(Mode.AUTO, Direction.CW, 1.0)
    controller.process_once()
    controller.stop()
    controller.process_once()
    validation_entered = Event()
    release_validation = Event()
    original_validator = controller._terminal_coherence_error

    def blocking_validator(status: StatusSnapshot, mode: Mode) -> str | None:
        validation_entered.set()
        assert release_validation.wait(1.0)
        return original_validator(status, mode)

    controller._terminal_coherence_error = blocking_validator  # type: ignore[method-assign]
    old_ack = ready_status(
        status_flags=0x000B,
        run_status=RunStatus.AUTOMATIC_ABORTED,
        start_ack_seq=1,
        stop_ack_seq=1,
    )
    reconcile_thread = Thread(target=lambda: controller._reconcile_start_cancellation(old_ack))
    reconcile_thread.start()
    assert validation_entered.wait(1.0)
    stop_done = Event()
    stop_thread = Thread(target=lambda: (controller.stop(), stop_done.set()))
    stop_thread.start()
    try:
        assert not stop_done.wait(0.1)
    finally:
        release_validation.set()
        reconcile_thread.join(1.0)
        stop_thread.join(1.0)

    assert stop_done.is_set()
    controller.process_once()
    assert controller._start_cancellation is not None
    assert controller._start_cancellation.stop_seq == 2
    with pytest.raises(CommandRejected):
        controller.start(Mode.MANUAL, Direction.CCW, 5.0)


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


@pytest.mark.parametrize("priority_action", ["stop", "disconnect", "shutdown"])
def test_fresh_status_callback_priority_action_cancels_exact_pending_start(
    priority_action: str,
) -> None:
    client = FakeClient()
    controller = make_controller(client)
    connect_controller(controller)
    armed = False

    def priority_callback(snapshot: ControllerSnapshot) -> None:
        nonlocal armed
        if not armed or snapshot.status is not client.status:
            return
        armed = False
        getattr(controller, priority_action)()

    controller.on_snapshot(priority_callback)
    controller.start(Mode.AUTO, Direction.CW, 1.0)
    armed = True
    client.calls.clear()

    controller.process_once()

    if priority_action == "shutdown":
        assert [call[0] for call in client.calls] == ["read_status", "close"]
        expected = "close"
    else:
        assert [call[0] for call in client.calls] == ["read_status"]
        controller.process_once()
        expected = "send_stop" if priority_action == "stop" else "close"
    assert expected in [call[0] for call in client.calls]
    assert "send_start" not in [call[0] for call in client.calls]


def test_concurrent_stop_at_fresh_status_barrier_prevents_start_write() -> None:
    client = FakeClient()
    controller = make_controller(client)
    connected = Event()
    fresh_callback_entered = Event()
    release_fresh_callback = Event()
    armed = False

    def callback(snapshot: ControllerSnapshot) -> None:
        if snapshot.connected:
            connected.set()
        if armed and snapshot.status is client.status:
            fresh_callback_entered.set()
            assert release_fresh_callback.wait(1.0)

    controller.on_snapshot(callback)
    controller.connect()
    controller.start_background()
    assert connected.wait(1.0)
    controller.start(Mode.AUTO, Direction.CW, 1.0)
    armed = True
    client.calls.clear()
    controller._wake.set()
    assert fresh_callback_entered.wait(1.0)

    controller.stop()
    client.call_event.clear()
    release_fresh_callback.set()
    assert client.call_event.wait(1.0)
    controller.shutdown(timeout=1.0)

    assert "send_start" not in [call[0] for call in client.calls]
    assert [call[0] for call in client.calls].count("send_stop") == 1


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
    client.observed_start_command_seq = 1

    controller.connect()
    controller.process_once()

    names = [call[0] for call in client.calls]
    assert names.count("send_start") == 1
    assert names.count("save_run") == 1
    assert names.count("acknowledge_buffer") == 1
    assert not controller.snapshot.download_pending


def test_exact_unknown_start_reconciles_mode_matching_running_state_without_replay() -> None:
    client = FakeClient()
    controller = make_controller(client)
    connect_controller(controller)
    client.fail_next["send_start"] = StartOutcomeUnknown("start response lost", 1)
    controller.start(Mode.AUTO, Direction.CW, 1.0)
    controller.process_once()
    client.observed_start_command_seq = 1
    client.status = ready_status(
        run_state=RunState.AUTO_RUNNING,
        start_ack_seq=1,
        run_status=RunStatus.RUNNING,
        run_start_plc_ms=123,
    )

    controller.connect()
    controller.process_once()

    assert controller._uncertain_start is None
    assert controller._run_session is not None
    assert controller._run_session.run_start_plc_ms == 123
    assert [call[0] for call in client.calls].count("send_start") == 1


def test_exact_unknown_start_clears_only_when_command_was_definitely_not_written() -> None:
    client = FakeClient()
    controller = make_controller(client)
    connect_controller(controller)
    client.fail_next["send_start"] = StartOutcomeUnknown("start response lost", 1)
    controller.start(Mode.AUTO, Direction.CW, 1.0)
    controller.process_once()
    client.observed_start_command_seq = 0
    client.status = ready_status(start_ack_seq=0, run_status=RunStatus.IDLE)

    controller.connect()
    controller.process_once()

    assert [call[0] for call in client.calls].count("send_start") == 1
    controller.start(Mode.MANUAL, Direction.CCW, 5.0)
    controller.process_once()
    assert [call[0] for call in client.calls].count("send_start") == 2


def test_exact_unknown_start_stays_blocked_while_command_is_pending_in_d1003() -> None:
    client = FakeClient()
    controller = make_controller(client)
    connect_controller(controller)
    client.fail_next["send_start"] = StartOutcomeUnknown("start response lost", 1)
    controller.start(Mode.AUTO, Direction.CW, 1.0)
    controller.process_once()
    client.observed_start_command_seq = 1
    client.status = ready_status(start_ack_seq=0, run_status=RunStatus.IDLE)

    controller.connect()
    controller.process_once()

    with pytest.raises(CommandRejected):
        controller.start(Mode.MANUAL, Direction.CCW, 5.0)
    assert [call[0] for call in client.calls].count("send_start") == 1
    assert "D1003" in (controller.snapshot.last_error or "")


def test_exact_unknown_start_treats_exact_ack_ready_idle_as_conflict() -> None:
    client = FakeClient()
    controller = make_controller(client)
    connect_controller(controller)
    client.fail_next["send_start"] = StartOutcomeUnknown("start response lost", 1)
    controller.start(Mode.AUTO, Direction.CW, 1.0)
    controller.process_once()
    client.observed_start_command_seq = 1
    client.status = ready_status(start_ack_seq=1, run_status=RunStatus.IDLE)

    controller.connect()
    controller.process_once()

    with pytest.raises(CommandRejected):
        controller.start(Mode.MANUAL, Direction.CCW, 5.0)
    assert [call[0] for call in client.calls].count("send_start") == 1
    assert "\u4eba\u5de5\u5bf9\u8d26" in (controller.snapshot.last_error or "")


def test_exact_unknown_start_clears_on_explicit_plc_start_rejection() -> None:
    client = FakeClient()
    controller = make_controller(client)
    connect_controller(controller)
    client.fail_next["send_start"] = StartOutcomeUnknown("start response lost", 1)
    controller.start(Mode.AUTO, Direction.CW, 1.0)
    controller.process_once()
    client.observed_start_command_seq = 1
    client.status = ready_status(
        run_state=RunState.FAULT,
        fault_code=10,
        start_ack_seq=1,
        run_status=RunStatus.FAULTED,
    )

    controller.connect()
    controller.process_once()
    assert controller._uncertain_start is None
    client.status = ready_status(start_ack_seq=1)
    controller.disconnect()
    controller.process_once()
    controller.connect()
    controller.process_once()
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
    client.observed_start_command_seq = 1

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
    client.observed_start_command_seq = 1

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


@pytest.mark.parametrize(
    ("mode", "terminal_status"),
    [
        (Mode.AUTO, RunStatus.MANUAL_STOPPED),
        (Mode.MANUAL, RunStatus.AUTOMATIC_ABORTED),
    ],
)
def test_terminal_status_must_be_compatible_with_session_mode(
    tmp_path: Path, mode: Mode, terminal_status: RunStatus
) -> None:
    client = FakeClient()
    clock = FakeClock()
    sync = ClockSynchronizer()
    sync.add_sample(1_000, 100, 1_000)
    store = FakeStore(tmp_path, client.calls)
    controller = make_controller(client, clock=clock, sync=sync, store=store)
    connect_controller(controller)
    controller.start(mode, Direction.CW, 1.0)
    controller.process_once()
    client.events = [EventRecord(1, 1, 1.0, 10)]
    client.status = ready_status(
        status_flags=0x000B,
        run_status=terminal_status,
        start_ack_seq=1,
        event_count=1,
        event_generation=39,
        run_start_plc_ms=100,
    )
    clock.advance(100)

    controller.process_once()

    names = [call[0] for call in client.calls]
    assert "save_run" not in names
    assert "acknowledge_buffer" not in names
    assert controller.snapshot.download_pending
    assert "mode" in (controller.snapshot.last_error or "")


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
        {"run_state": RunState.AUTO_RUNNING},
    ],
    ids=("start-ack", "generation", "event-count", "terminal-status", "run-start-tick", "run-state"),
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


def test_background_mode_is_reserved_before_worker_can_claim_io_thread() -> None:
    client = FakeClient()
    controller = make_controller(client)
    caller_thread = get_ident()
    worker_entered = Event()
    release_worker = Event()
    connected = Event()
    controller.on_snapshot(lambda snapshot: connected.set() if snapshot.connected else None)
    original_worker_loop = controller._worker_loop

    def delayed_worker_loop() -> None:
        worker_entered.set()
        assert release_worker.wait(1.0)
        original_worker_loop()

    controller._worker_loop = delayed_worker_loop  # type: ignore[method-assign]
    controller.connect()
    controller.start_background()
    assert worker_entered.wait(1.0)
    try:
        with pytest.raises(ControllerStopped):
            controller.process_once()
    finally:
        release_worker.set()

    assert connected.wait(1.0)
    controller.shutdown(timeout=1.0)
    assert len(set(client.io_threads)) == 1
    assert client.io_threads[0] != caller_thread


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
