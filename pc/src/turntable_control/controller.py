"""Qt-independent single-thread coordinator for safe turntable sessions."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Event, RLock, Thread, get_ident
from typing import Callable

from .domain import (
    Direction,
    Mode,
    MotionRejected,
    RunState,
    RunStatus,
    SPEEDS_DEG_S,
    automatic_target,
    manual_target,
)
from .csv_store import RunExport, RunMetadata
from .modbus_client import (
    CommunicationError,
    ProtocolMismatch,
    StartNotIssued,
    StartOutcomeUnknown,
    StatusSnapshot,
)


STATUS_ZERO_VALID = 0x0001
STATUS_POWERED = 0x0002
STATUS_BUFFER_READY = 0x0008
FAULT_START_REJECTED = 10
_QUEUE_LIMIT = 32


class CommandRejected(RuntimeError):
    """A user command cannot be safely queued against the latest snapshot."""


class ControllerStopped(RuntimeError):
    """The controller worker has stopped or could not be stopped safely."""


@dataclass(frozen=True)
class ControllerSnapshot:
    connected: bool = False
    status: StatusSnapshot | None = None
    last_error: str | None = None
    active_test_id: str | None = None
    download_pending: bool = False
    saved_csv: Path | None = None


@dataclass(frozen=True)
class _StartCommand:
    mode: Mode
    direction: Direction
    speed_deg_s: float
    speed_index: int


@dataclass(frozen=True)
class _RunSession:
    mode: Mode
    direction: Direction
    speed_deg_s: float
    requested_epoch_ms: int
    session_token: int
    start_seq: int
    run_start_plc_ms: int | None = None


@dataclass(frozen=True)
class _UncertainStart:
    command: _StartCommand
    start_seq: int | None
    requested_epoch_ms: int


@dataclass(frozen=True)
class _StartCancellation:
    start_seq: int | None
    mode: Mode | None
    stop_seq: int | None = None
    require_terminal: bool = False


@dataclass(frozen=True)
class _DurableRecovery:
    session_token: int
    start_ack_seq: int
    generation: int
    event_count: int
    run_state: RunState
    run_status: RunStatus
    run_start_plc_ms: int
    test_id: str
    path: Path

    def matches(
        self,
        status: StatusSnapshot,
        session: _RunSession | None,
        snapshot: ControllerSnapshot,
    ) -> bool:
        return (
            session is not None
            and session.session_token == self.session_token
            and snapshot.active_test_id == self.test_id
            and snapshot.saved_csv == self.path
            and status.start_ack_seq == self.start_ack_seq
            and status.event_generation == self.generation
            and status.event_count == self.event_count
            and status.run_state is self.run_state
            and status.run_status is self.run_status
            and status.run_start_plc_ms == self.run_start_plc_ms
        )


_TERMINAL_RUN_STATUSES = frozenset(
    (
        RunStatus.COMPLETED,
        RunStatus.MANUAL_STOPPED,
        RunStatus.AUTOMATIC_ABORTED,
        RunStatus.COMMUNICATION_ABORTED,
        RunStatus.FAULTED,
    )
)


class TurntableController:
    """Serialize all client and CSV I/O through ``process_once`` or one worker."""

    def __init__(
        self,
        client: object,
        csv_store: object,
        clock_sync: object,
        *,
        epoch_ms: Callable[[], int],
        monotonic_ms: Callable[[], int],
        poll_interval_ms: int = 100,
        heartbeat_interval_ms: int = 250,
    ) -> None:
        if type(poll_interval_ms) is not int or poll_interval_ms <= 0:
            raise ValueError("poll_interval_ms must be a positive integer")
        if type(heartbeat_interval_ms) is not int or heartbeat_interval_ms <= 0:
            raise ValueError("heartbeat_interval_ms must be a positive integer")
        self._client = client
        self._csv_store = csv_store
        self._clock_sync = clock_sync
        self._epoch_ms = epoch_ms
        self._monotonic_ms = monotonic_ms
        self._poll_interval_ms = poll_interval_ms
        self._heartbeat_interval_ms = heartbeat_interval_ms
        self._lock = RLock()
        self._commands: deque[tuple[str, object | None]] = deque()
        self._stop_requested = False
        self._disconnect_requested = False
        self._snapshot = ControllerSnapshot()
        self._snapshot_callbacks: list[Callable[[ControllerSnapshot], object]] = []
        self._error_callbacks: list[Callable[[str], object]] = []
        self._saved_callbacks: list[Callable[[Path], object]] = []
        self._wake = Event()
        self._shutdown_requested = Event()
        self._worker: Thread | None = None
        self._terminal_failure: Exception | None = None
        self._io_mode: str | None = None
        self._io_thread_id: int | None = None
        self._next_poll_ms: int | None = None
        self._next_heartbeat_ms: int | None = None
        self._heartbeat = 0
        self._pending_sync: tuple[int, int] | None = None
        self._observed_start_command_seq: int | None = None
        self._run_session: _RunSession | None = None
        self._pending_start: _StartCommand | None = None
        self._issued_start_seq: int | None = None
        self._uncertain_start: _UncertainStart | None = None
        self._start_cancellation: _StartCancellation | None = None
        self._attempted_generations: set[int] = set()
        self._handled_generations: set[int] = set()
        self._generation_test_ids: dict[int, str] = {}
        self._verified_session = 0
        self._ack_attempts: set[tuple[int, int]] = set()
        self._durable_recovery: _DurableRecovery | None = None
        self._next_session_token = 0

    @property
    def snapshot(self) -> ControllerSnapshot:
        with self._lock:
            return self._snapshot

    def on_snapshot(self, callback: Callable[[ControllerSnapshot], object]) -> Callable[[], None]:
        return self._register(self._snapshot_callbacks, callback)

    def on_error(self, callback: Callable[[str], object]) -> Callable[[], None]:
        return self._register(self._error_callbacks, callback)

    def on_run_saved(self, callback: Callable[[Path], object]) -> Callable[[], None]:
        return self._register(self._saved_callbacks, callback)

    def connect(self) -> None:
        with self._lock:
            self._ensure_running()
            if self._snapshot.connected or any(name == "connect" for name, _ in self._commands):
                return
            self._enqueue_locked("connect", None)

    def disconnect(self) -> None:
        with self._lock:
            self._ensure_running()
            self._commands.clear()
            if (
                self._stop_requested
                and self._start_cancellation is not None
                and self._start_cancellation.start_seq is None
                and self._start_cancellation.stop_seq is None
            ):
                self._start_cancellation = None
            self._stop_requested = False
            self._disconnect_requested = True
            self._wake.set()

    def start(self, mode: Mode, direction: Direction, speed_deg_s: float) -> None:
        with self._lock:
            self._ensure_running()
            if self._disconnect_requested:
                raise CommandRejected("PLC未连接")
            if self._uncertain_start is not None:
                raise CommandRejected("上次启动写入结果未知，禁止自动重试运动")
            if self._start_cancellation is not None:
                raise CommandRejected("已发出的启动正在等待STOP/START确认，禁止新START")
            if self._issued_start_seq is not None:
                raise CommandRejected("启动命令正在等待PLC确认")
            if self._pending_start is not None:
                raise CommandRejected("启动命令正在等待PLC确认")
            if self._run_session is not None:
                raise CommandRejected("当前运行会话尚未封存，禁止新START")
            command = self._validated_start(mode, direction, speed_deg_s, self._snapshot)
            self._pending_start = command
            try:
                self._enqueue_locked("start", command)
            except Exception:
                self._pending_start = None
                raise

    def stop(self) -> None:
        with self._lock:
            self._ensure_running()
            if not self._snapshot.connected or self._disconnect_requested:
                raise CommandRejected("PLC未连接")
            self._commands = deque((name, value) for name, value in self._commands if name != "start")
            if self._issued_start_seq is not None:
                session = self._run_session
                self._start_cancellation = _StartCancellation(
                    start_seq=self._issued_start_seq,
                    mode=None if session is None else session.mode,
                )
                self._issued_start_seq = None
            elif (
                self._start_cancellation is None
                and self._run_session is not None
                and (
                    self._run_session.run_start_plc_ms is not None
                    or (
                        self._snapshot.status is not None
                        and (
                            self._snapshot.status.run_status is RunStatus.RUNNING
                            or self._snapshot.status.run_state is RunState.STOPPING
                        )
                    )
                )
            ):
                self._start_cancellation = _StartCancellation(
                    start_seq=self._run_session.start_seq,
                    mode=self._run_session.mode,
                    require_terminal=True,
                )
            elif self._start_cancellation is None:
                self._start_cancellation = _StartCancellation(start_seq=None, mode=None)
            self._pending_start = None
            self._stop_requested = True
            self._wake.set()

    def set_zero(self) -> None:
        self._enqueue_connected("set_zero")

    def reset_alarm(self) -> None:
        self._enqueue_connected("reset_alarm")

    def toggle_power(self) -> None:
        self._enqueue_connected("toggle_power")

    def retry_download(self) -> None:
        if not self.snapshot.download_pending:
            raise CommandRejected("没有待重试的数据")
        self._enqueue_connected("retry_download")

    def process_once(self) -> None:
        self._claim_io_thread()
        try:
            self._process_once_impl()
        except CommandRejected as error:
            self._report_error(str(error))
        except (CommunicationError, ProtocolMismatch) as error:
            self._handle_communication_error(error)

    def _process_once_impl(self) -> None:
        if self._shutdown_requested.is_set():
            self._finalize_shutdown()
            return
        if self._disconnect_requested:
            self._process_disconnect()
            return
        if self._stop_requested:
            with self._lock:
                self._stop_requested = False
            stop_seq = self._client.send_stop()
            with self._lock:
                if self._start_cancellation is not None:
                    self._start_cancellation = replace(
                        self._start_cancellation, stop_seq=stop_seq
                    )
            return
        command: tuple[str, object | None] | None = None
        with self._lock:
            if self._commands:
                command = self._commands.popleft()
        if command is None:
            if self.snapshot.connected:
                self._run_scheduled()
            return
        name, value = command
        if name == "connect":
            self._process_connect()
        elif name == "start":
            assert isinstance(value, _StartCommand)
            fresh = self._client.read_status()
            self._accept_status(fresh)
            try:
                self._validated_start(value.mode, value.direction, value.speed_deg_s, self.snapshot)
            except CommandRejected:
                self._pending_start = None
                raise
            with self._lock:
                start_cancelled = (
                    self._shutdown_requested.is_set()
                    or self._stop_requested
                    or self._disconnect_requested
                    or self._pending_start is not value
                )
                if start_cancelled:
                    if self._pending_start is value:
                        self._pending_start = None
                    return
                self._attempted_generations.clear()
                self._handled_generations.clear()
                self._generation_test_ids.clear()
                self._ack_attempts.clear()
                requested_epoch = self._epoch_ms()
                try:
                    start_seq = self._client.send_start(
                        value.mode, value.direction, value.speed_index
                    )
                except StartNotIssued:
                    self._pending_start = None
                    self._run_session = None
                    raise
                except StartOutcomeUnknown as error:
                    self._pending_start = None
                    self._run_session = None
                    self._uncertain_start = _UncertainStart(
                        value, error.start_seq, requested_epoch
                    )
                    raise
                except CommunicationError:
                    self._pending_start = None
                    self._run_session = None
                    self._uncertain_start = _UncertainStart(value, None, requested_epoch)
                    raise
                self._next_session_token += 1
                self._run_session = _RunSession(
                    value.mode,
                    value.direction,
                    value.speed_deg_s,
                    requested_epoch,
                    self._next_session_token,
                    start_seq=start_seq,
                )
                self._issued_start_seq = start_seq
            self._publish(replace(self.snapshot, status=fresh, active_test_id=f"run_{requested_epoch}", last_error=None))
            self._request_time_sync()
        elif name == "set_zero":
            self._client.set_zero()
        elif name == "reset_alarm":
            self._client.reset_alarm()
        elif name == "toggle_power":
            self._client.toggle_power()
        elif name == "retry_download":
            status = self._client.read_status()
            self._accept_status(status)
            self._attempt_download(status, explicit=True)
        if self.snapshot.connected:
            self._run_scheduled()

    def start_background(self) -> None:
        with self._lock:
            self._ensure_running()
            if self._worker is not None and self._worker.is_alive():
                return
            if self._io_mode == "deterministic":
                raise ControllerStopped(
                    "确定性process_once模式已占用I/O线程，禁止切换到后台线程"
                )
            if self._io_mode is not None:
                raise ControllerStopped("控制器I/O模式已被占用")
            self._io_mode = "background"
            self._worker = Thread(target=self._worker_loop, name="turntable-controller", daemon=True)
            try:
                self._worker.start()
            except Exception:
                self._worker = None
                self._io_mode = None
                raise

    def shutdown(self, timeout: float | None = None) -> None:
        with self._lock:
            self._shutdown_requested.set()
            self._wake.set()
            worker = self._worker
        if worker is not None:
            worker.join(timeout)
            if worker.is_alive():
                raise ControllerStopped("控制器线程未能在超时内停止")
        else:
            self.process_once()
        failure = self._terminal_failure
        if failure is not None:
            raise ControllerStopped(str(failure)) from failure

    def _process_connect(self) -> None:
        self._client.connect()
        self._observed_start_command_seq = self._client.start_command_seq
        status = self._client.read_status()
        self._verified_session += 1
        now = self._monotonic_ms()
        self._heartbeat = status.heartbeat_echo
        self._next_poll_ms = now + self._poll_interval_ms
        self._next_heartbeat_ms = now + self._heartbeat_interval_ms
        self._publish(replace(self.snapshot, connected=True, status=status, last_error=None))
        self._accept_status(status, reconnect=True)
        self._request_time_sync()

    def _process_disconnect(self) -> None:
        with self._lock:
            self._disconnect_requested = False
            self._commands.clear()
            self._stop_requested = False
            self._pending_start = None
        if self.snapshot.connected:
            self._client.close()
        self._pending_sync = None
        self._observed_start_command_seq = None
        self._next_poll_ms = None
        self._next_heartbeat_ms = None
        self._publish(replace(self.snapshot, connected=False))

    def _request_time_sync(self) -> None:
        sent = self._epoch_ms()
        sequence = self._client.request_time_sync()
        self._pending_sync = (sequence, sent)

    def _run_scheduled(self) -> None:
        now = self._monotonic_ms()
        if self._next_poll_ms is not None and now >= self._next_poll_ms:
            status = self._client.read_status()
            self._accept_status(status)
            while self._next_poll_ms <= now:
                self._next_poll_ms += self._poll_interval_ms
        if self._next_heartbeat_ms is not None and now >= self._next_heartbeat_ms:
            self._heartbeat = (self._heartbeat + 1) & 0xFFFF
            self._client.write_heartbeat(self._heartbeat)
            while self._next_heartbeat_ms <= now:
                self._next_heartbeat_ms += self._heartbeat_interval_ms

    def _accept_status(self, status: StatusSnapshot, *, reconnect: bool = False) -> None:
        sync_error: str | None = None
        session_error: str | None = None
        reconciliation_error, reconciled_test_id = self._reconcile_uncertain_start(status)
        cancellation_error = self._reconcile_start_cancellation(status)
        recovery = self._durable_recovery
        if recovery is not None and not status.status_flags & STATUS_BUFFER_READY:
            self._seal_run_session(recovery.session_token)
        pending = self._pending_sync
        if pending is not None and status.time_sync_response_seq == pending[0]:
            self._pending_sync = None
            try:
                self._clock_sync.add_sample(pending[1], status.plc_tick_ms, self._epoch_ms())
            except Exception as error:
                sync_error = f"clock synchronization sample rejected: {error}"
        session = self._run_session
        terminal = status.run_status in _TERMINAL_RUN_STATUSES
        confirmed_missing_terminal = (
            session is not None
            and session.run_start_plc_ms is not None
            and status.start_ack_seq == session.start_seq
            and status.run_state is RunState.READY
            and status.run_status is RunStatus.IDLE
            and not status.status_flags & STATUS_BUFFER_READY
        )
        if confirmed_missing_terminal:
            session_error = (
                "PLC active session terminal evidence is incoherent: "
                f"{self._terminal_coherence_error(status, session.mode)}"
            )
        elif session is not None and (status.run_status is RunStatus.RUNNING or terminal):
            if status.start_ack_seq == session.start_seq:
                buffer_ready = bool(status.status_flags & STATUS_BUFFER_READY)
                expected_running_state = (
                    RunState.AUTO_RUNNING if session.mode is Mode.AUTO else RunState.MANUAL_RUNNING
                )
                accepted_running = (
                    status.run_status is RunStatus.RUNNING
                    and status.run_state is expected_running_state
                    and not buffer_ready
                )
                accepted_terminal = terminal and self._terminal_coherence_error(
                    status, session.mode
                ) is None
                explicitly_rejected = (
                    status.run_status is RunStatus.FAULTED
                    and status.run_state in (RunState.FAULT, RunState.ZERO_REQUIRED)
                    and status.fault_code == FAULT_START_REJECTED
                    and not buffer_ready
                )
                if not (accepted_running or accepted_terminal or explicitly_rejected):
                    session_error = "PLC完整指纹不一致：START_ACK与session mode/state/buffer冲突"
                elif explicitly_rejected:
                    with self._lock:
                        if self._run_session is session:
                            self._run_session = None
                            self._pending_start = None
                            self._issued_start_seq = None
                else:
                    with self._lock:
                        if session.run_start_plc_ms is None:
                            self._run_session = replace(
                                session, run_start_plc_ms=status.run_start_plc_ms
                            )
                        elif session.run_start_plc_ms != status.run_start_plc_ms:
                            session_error = (
                                "PLC完整指纹不一致（run-start tick冲突）；禁止保存或确认缓冲区"
                            )
                        if session_error is None:
                            self._pending_start = None
                            self._issued_start_seq = None
        self._publish(
            replace(
                self.snapshot,
                status=status,
                active_test_id=reconciled_test_id or self.snapshot.active_test_id,
                last_error=session_error or cancellation_error or sync_error or reconciliation_error,
            )
        )
        if session_error is not None:
            if status.status_flags & STATUS_BUFFER_READY:
                self._publish(
                    replace(self.snapshot, download_pending=True, last_error=session_error)
                )
            self._notify_error(session_error)
            return
        if cancellation_error is not None:
            self._notify_error(cancellation_error)
        elif sync_error is not None:
            self._notify_error(sync_error)
        elif reconciliation_error is not None:
            self._notify_error(reconciliation_error)
        self._attempt_download(status, reconnect=reconnect)

    def _reconcile_start_cancellation(self, status: StatusSnapshot) -> str | None:
        with self._lock:
            return self._reconcile_start_cancellation_locked(status)

    def _reconcile_start_cancellation_locked(self, status: StatusSnapshot) -> str | None:
        cancellation = self._start_cancellation
        if cancellation is None or cancellation.stop_seq is None:
            return
        if status.stop_ack_seq != cancellation.stop_seq:
            return
        if cancellation.start_seq is not None and status.start_ack_seq != cancellation.start_seq:
            return
        buffer_ready = bool(status.status_flags & STATUS_BUFFER_READY)
        stopped_idle = (
            status.run_state is RunState.READY
            and status.run_status is RunStatus.IDLE
            and not buffer_ready
        )
        stop_only_retained_terminal = (
            cancellation.start_seq is None
            and status.run_state is RunState.READY
            and status.run_status in _TERMINAL_RUN_STATUSES
            and not buffer_ready
        )
        explicitly_rejected = (
            cancellation.start_seq is not None
            and status.run_state in (RunState.FAULT, RunState.ZERO_REQUIRED)
            and status.run_status is RunStatus.FAULTED
            and status.fault_code == FAULT_START_REJECTED
            and not buffer_ready
        )
        terminal_error = (
            None
            if cancellation.mode is None
            else self._terminal_coherence_error(status, cancellation.mode)
        )
        terminal_stopped = cancellation.mode is not None and terminal_error is None
        if cancellation.require_terminal and not terminal_stopped:
            contradictory_stopped = (
                status.run_state is RunState.READY
                or status.run_status in _TERMINAL_RUN_STATUSES
                or buffer_ready
            )
            if contradictory_stopped:
                return f"PLC active STOP terminal evidence is incoherent: {terminal_error}"
            return None
        if not (
            stopped_idle
            or stop_only_retained_terminal
            or explicitly_rejected
            or terminal_stopped
        ):
            return
        if self._stop_requested:
            return
        self._start_cancellation = None
        self._pending_start = None
        if not buffer_ready:
            self._run_session = None
        return None

    def _reconcile_uncertain_start(self, status: StatusSnapshot) -> tuple[str | None, str | None]:
        with self._lock:
            return self._reconcile_uncertain_start_locked(status)

    def _reconcile_uncertain_start_locked(
        self, status: StatusSnapshot
    ) -> tuple[str | None, str | None]:
        uncertain = self._uncertain_start
        if uncertain is None:
            return None, None
        if uncertain.start_seq is None:
            return "启动序号不可知，禁止自动归属；需要人工对账", None
        command_seq = self._observed_start_command_seq
        if command_seq is None:
            return "缺少重连后D1003 START_SEQ观测值；需要人工对账", None
        buffer_ready = bool(status.status_flags & STATUS_BUFFER_READY)
        expected_running_state = (
            RunState.AUTO_RUNNING if uncertain.command.mode is Mode.AUTO else RunState.MANUAL_RUNNING
        )
        exact_command = command_seq == uncertain.start_seq
        exact_ack = status.start_ack_seq == uncertain.start_seq
        consistent_running = (
            exact_command
            and exact_ack
            and status.run_status is RunStatus.RUNNING
            and status.run_state is expected_running_state
            and not buffer_ready
        )
        terminal_error = self._terminal_coherence_error(status, uncertain.command.mode)
        consistent_terminal = (
            exact_command
            and exact_ack
            and terminal_error is None
        )
        if consistent_running or consistent_terminal:
            self._next_session_token += 1
            self._run_session = _RunSession(
                mode=uncertain.command.mode,
                direction=uncertain.command.direction,
                speed_deg_s=uncertain.command.speed_deg_s,
                requested_epoch_ms=uncertain.requested_epoch_ms,
                session_token=self._next_session_token,
                start_seq=uncertain.start_seq,
                run_start_plc_ms=status.run_start_plc_ms,
            )
            self._uncertain_start = None
            self._pending_start = None
            return None, f"run_{uncertain.requested_epoch_ms}"
        explicit_rejection = (
            exact_command
            and exact_ack
            and not buffer_ready
            and status.run_status is RunStatus.FAULTED
            and status.fault_code == FAULT_START_REJECTED
            and status.run_state in (RunState.FAULT, RunState.ZERO_REQUIRED)
        )
        if explicit_rejection:
            self._uncertain_start = None
            self._run_session = None
            self._pending_start = None
            return None, None
        definitely_not_accepted = (
            not exact_command
            and not exact_ack
            and status.run_state is RunState.READY
            and status.run_status is RunStatus.IDLE
            and not buffer_ready
        )
        if definitely_not_accepted:
            self._uncertain_start = None
            self._run_session = None
            self._pending_start = None
            return None, None
        if exact_command and not exact_ack:
            return "D1003 START_SEQ仍为待确认启动序号；禁止新START，需要人工对账", None
        return "启动确认序号或PLC状态/缓冲区与不确定START冲突；需要人工对账", None

    def _attempt_download(
        self, status: StatusSnapshot, *, explicit: bool = False, reconnect: bool = False
    ) -> None:
        generation = status.event_generation
        buffer_ready = bool(status.status_flags & STATUS_BUFFER_READY)
        recovery = self._durable_recovery
        if recovery is not None:
            if not buffer_ready:
                self._seal_run_session(recovery.session_token)
                self._handled_generations.add(recovery.generation)
                self._durable_recovery = None
                self._publish(replace(self.snapshot, download_pending=False, active_test_id=None))
            elif reconnect or explicit:
                session = self._run_session
                coherence_error = (
                    "缺少已保存CSV对应的运行会话"
                    if session is None
                    else self._terminal_coherence_error(status, session.mode)
                )
                if coherence_error is not None:
                    self._download_failed(f"PLC缓冲区完整指纹不一致: {coherence_error}")
                    return
                if not recovery.matches(status, self._run_session, self.snapshot):
                    self._download_failed("PLC缓冲区与已保存CSV的完整指纹不一致，禁止确认；需要人工对账")
                    return
                self._acknowledge_recovery(recovery)
            return
        if generation in self._handled_generations:
            return
        if not buffer_ready or status.run_status not in _TERMINAL_RUN_STATUSES:
            return
        if generation in self._attempted_generations and not explicit:
            return
        self._attempted_generations.add(generation)
        session = self._run_session
        if session is None and self._uncertain_start is not None:
            self._download_failed(
                "启动写入结果未知，禁止自动归属、保存或确认PLC缓冲区；需要人工对账"
            )
            return
        if session is None or status.start_ack_seq != session.start_seq:
            self._download_failed("缺少本次运行会话信息")
            return
        coherence_error = self._terminal_coherence_error(status, session.mode)
        if coherence_error is not None:
            self._download_failed(coherence_error)
            return
        if not 1 <= status.event_count <= 360:
            self._download_failed("PLC事件数量必须在1..360")
            return
        test_id = self._generation_test_ids.setdefault(
            generation, f"run_{session.requested_epoch_ms}_g{generation}"
        )
        metadata = RunMetadata(
            test_id=test_id,
            mode=session.mode,
            direction=session.direction,
            speed_deg_s=session.speed_deg_s,
            total_ratio=50.0,
            acceleration_deg_s2=5.0,
            deceleration_deg_s2=5.0,
            stop_deceleration_deg_s2=10.0,
            run_status=status.run_status,
            run_start_plc_ms=status.run_start_plc_ms,
            saved_at_epoch_ms=self._epoch_ms(),
        )
        self._publish(replace(self.snapshot, download_pending=True, last_error=None))
        try:
            events = self._client.read_events(status.event_count)
            self._validate_event_integrity(events, status, session)
            path = Path(self._csv_store.save_run(RunExport(metadata=metadata, events=events), self._clock_sync))
        except (CommunicationError, ProtocolMismatch):
            raise
        except Exception as error:
            self._download_failed(str(error))
            return
        recovery = _DurableRecovery(
            session_token=session.session_token,
            start_ack_seq=status.start_ack_seq,
            generation=generation,
            event_count=status.event_count,
            run_state=status.run_state,
            run_status=status.run_status,
            run_start_plc_ms=status.run_start_plc_ms,
            test_id=test_id,
            path=path,
        )
        self._durable_recovery = recovery
        self._publish(
            replace(
                self.snapshot,
                active_test_id=test_id,
                download_pending=True,
                saved_csv=path,
                last_error=None,
            )
        )
        self._notify_run_saved(path)
        self._acknowledge_recovery(recovery)

    @staticmethod
    def _terminal_coherence_error(status: StatusSnapshot, mode: Mode) -> str | None:
        if status.run_status not in _TERMINAL_RUN_STATUSES:
            return "PLC status is not terminal"
        if not status.status_flags & STATUS_BUFFER_READY:
            return "PLC terminal status has no retained buffer"
        expected_state = RunState.FAULT if status.run_status is RunStatus.FAULTED else RunState.READY
        if status.run_state is not expected_state:
            return "PLC terminal state is inconsistent with terminal status"
        if mode is Mode.AUTO and status.run_status is RunStatus.MANUAL_STOPPED:
            return "PLC terminal status is incompatible with AUTO session mode"
        if mode is Mode.MANUAL and status.run_status is RunStatus.AUTOMATIC_ABORTED:
            return "PLC terminal status is incompatible with MANUAL session mode"
        return None

    @staticmethod
    def _validate_event_integrity(
        events: object, status: StatusSnapshot, session: _RunSession
    ) -> None:
        try:
            records = tuple(events)  # type: ignore[arg-type]
        except TypeError as error:
            raise ValueError("PLC事件列表无效") from error
        if len(records) != status.event_count:
            raise ValueError("PLC事件数量与读取结果不一致，禁止确认缓冲区")
        for expected, event in enumerate(records, start=1):
            if getattr(event, "sequence", None) != expected:
                raise ValueError("PLC事件序号必须严格等于1..N，禁止确认缓冲区")
            if getattr(event, "travel_angle_deg", None) != expected:
                raise ValueError("PLC行程角度必须严格等于事件序号，禁止确认缓冲区")
        if session.mode is Mode.AUTO and status.run_status is RunStatus.COMPLETED and len(records) != 360:
            raise ValueError("AUTO COMPLETED必须包含完整360条1..360度事件，禁止确认缓冲区")

    def _acknowledge_recovery(self, recovery: _DurableRecovery) -> None:
        key = (self._verified_session, recovery.generation)
        if key in self._ack_attempts:
            return
        self._ack_attempts.add(key)
        self._client.acknowledge_buffer()
        self._seal_run_session(recovery.session_token)
        self._handled_generations.add(recovery.generation)
        self._durable_recovery = None
        self._publish(replace(self.snapshot, download_pending=False, active_test_id=None, last_error=None))

    def _seal_run_session(self, session_token: int) -> None:
        with self._lock:
            session = self._run_session
            if session is None or session.session_token != session_token:
                return
            self._run_session = None
            if self._issued_start_seq == session.start_seq:
                self._issued_start_seq = None
            cancellation = self._start_cancellation
            if (
                cancellation is not None
                and cancellation.require_terminal
                and cancellation.start_seq == session.start_seq
            ):
                self._start_cancellation = _StartCancellation(
                    start_seq=None,
                    mode=None,
                    stop_seq=cancellation.stop_seq,
                )

    def _download_failed(self, message: str) -> None:
        self._publish(replace(self.snapshot, download_pending=True, last_error=message))
        self._notify_error(message)

    def _notify_run_saved(self, path: Path) -> None:
        with self._lock:
            callbacks = tuple(self._saved_callbacks)
        for callback in callbacks:
            try:
                callback(path)
            except Exception as error:
                self._notify_error(f"保存回调失败: {error}")

    def _notify_error(self, message: str) -> None:
        with self._lock:
            callbacks = tuple(self._error_callbacks)
        for callback in callbacks:
            try:
                callback(message)
            except Exception:
                pass

    def _validated_start(
        self, mode: Mode, direction: Direction, speed_deg_s: float, snapshot: ControllerSnapshot
    ) -> _StartCommand:
        if not snapshot.connected or snapshot.status is None:
            raise CommandRejected("PLC未连接")
        if type(mode) is not Mode or type(direction) is not Direction:
            raise CommandRejected("模式或方向无效")
        if type(speed_deg_s) not in (int, float) or isinstance(speed_deg_s, bool):
            raise CommandRejected("速度档位无效")
        speed = float(speed_deg_s)
        if speed not in SPEEDS_DEG_S:
            raise CommandRejected("速度档位无效")
        status = snapshot.status
        if not status.status_flags & STATUS_ZERO_VALID:
            raise CommandRejected("未设零")
        if not status.status_flags & STATUS_POWERED:
            raise CommandRejected("伺服未就绪")
        if status.status_flags & STATUS_BUFFER_READY or snapshot.download_pending:
            raise CommandRejected("上次数据尚未保存")
        if status.run_state is not RunState.READY:
            raise CommandRejected("转台当前不可启动")
        try:
            target = automatic_target(status.actual_position_deg, direction) if mode is Mode.AUTO else manual_target(
                status.actual_position_deg, direction
            )
        except MotionRejected as error:
            raise CommandRejected(str(error)) from error
        if target == status.actual_position_deg:
            raise CommandRejected("该方向空间不足，请反向运行")
        return _StartCommand(mode, direction, speed, SPEEDS_DEG_S.index(speed) + 1)

    def _enqueue_connected(self, name: str) -> None:
        with self._lock:
            self._ensure_running()
            if not self._snapshot.connected or self._disconnect_requested:
                raise CommandRejected("PLC未连接")
            self._enqueue_locked(name, None)

    def _enqueue_locked(self, name: str, value: object | None) -> None:
        if len(self._commands) >= _QUEUE_LIMIT:
            raise CommandRejected("命令队列已满")
        self._commands.append((name, value))
        self._wake.set()

    def _ensure_running(self) -> None:
        if self._shutdown_requested.is_set():
            raise ControllerStopped("控制器已停止")

    def _claim_io_thread(self) -> None:
        current = get_ident()
        with self._lock:
            if self._io_mode is None:
                self._io_mode = "deterministic"
                self._io_thread_id = current
                return
            if self._io_mode == "background":
                if self._worker is None or self._worker.ident != current:
                    raise ControllerStopped("后台I/O模式禁止外部process_once调用")
            if self._io_thread_id is None:
                self._io_thread_id = current
            elif self._io_thread_id != current:
                raise ControllerStopped("所有 I/O 必须由同一控制线程执行")

    def _register(self, callbacks: list[Callable[..., object]], callback: Callable[..., object]) -> Callable[[], None]:
        if not callable(callback):
            raise TypeError("callback must be callable")
        with self._lock:
            callbacks.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                if callback in callbacks:
                    callbacks.remove(callback)

        return unsubscribe

    def _publish(self, snapshot: ControllerSnapshot) -> None:
        with self._lock:
            self._snapshot = snapshot
            callbacks = tuple(self._snapshot_callbacks)
        for callback in callbacks:
            try:
                callback(snapshot)
            except Exception as error:
                self._notify_error(f"状态回调失败: {error}")

    def _report_error(self, message: str) -> None:
        self._publish(replace(self.snapshot, last_error=message))
        with self._lock:
            callbacks = tuple(self._error_callbacks)
        for callback in callbacks:
            try:
                callback(message)
            except Exception:
                pass

    def _handle_communication_error(self, error: Exception) -> None:
        try:
            self._client.close()
        except Exception:
            pass
        with self._lock:
            self._commands.clear()
            self._stop_requested = False
            self._disconnect_requested = False
            self._pending_start = None
        self._pending_sync = None
        self._observed_start_command_seq = None
        self._next_poll_ms = None
        self._next_heartbeat_ms = None
        self._publish(replace(self.snapshot, connected=False, last_error=str(error)))
        with self._lock:
            callbacks = tuple(self._error_callbacks)
        for callback in callbacks:
            try:
                callback(str(error))
            except Exception:
                pass

    def _worker_loop(self) -> None:
        failure: Exception | None = None
        try:
            while not self._shutdown_requested.is_set():
                self.process_once()
                self._wake.wait(0.05)
                self._wake.clear()
        except Exception as error:
            failure = error
        finally:
            self._finalize_shutdown(failure)

    def _finalize_shutdown(self, prior_failure: Exception | None = None) -> None:
        with self._lock:
            self._commands.clear()
            self._stop_requested = False
            self._disconnect_requested = False
            self._pending_start = None
        close_failure: Exception | None = None
        try:
            if self.snapshot.connected and self._io_thread_id == get_ident():
                self._client.close()
        except Exception as error:
            close_failure = error
        self._pending_sync = None
        self._next_poll_ms = None
        self._next_heartbeat_ms = None
        failure = prior_failure or close_failure
        if failure is not None:
            self._terminal_failure = failure
        self._publish(
            replace(
                self.snapshot,
                connected=False,
                last_error=str(failure) if failure is not None else self.snapshot.last_error,
            )
        )
        if failure is not None:
            self._notify_error(str(failure))
