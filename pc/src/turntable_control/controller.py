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
from .modbus_client import CommunicationError, ProtocolMismatch, StatusSnapshot


STATUS_ZERO_VALID = 0x0001
STATUS_POWERED = 0x0002
STATUS_BUFFER_READY = 0x0008
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
    start_seq: int | None = None
    run_start_plc_ms: int | None = None


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
        self._io_thread_id: int | None = None
        self._next_poll_ms: int | None = None
        self._next_heartbeat_ms: int | None = None
        self._heartbeat = 0
        self._pending_sync: tuple[int, int] | None = None
        self._run_session: _RunSession | None = None
        self._attempted_generations: set[int] = set()
        self._saved_generations: dict[int, Path] = {}
        self._handled_generations: set[int] = set()
        self._generation_test_ids: dict[int, str] = {}
        self._verified_session = 0
        self._ack_attempts: set[tuple[int, int]] = set()

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
            self._stop_requested = False
            self._disconnect_requested = True
            self._wake.set()

    def start(self, mode: Mode, direction: Direction, speed_deg_s: float) -> None:
        with self._lock:
            self._ensure_running()
            if self._disconnect_requested:
                raise CommandRejected("PLC未连接")
            command = self._validated_start(mode, direction, speed_deg_s, self._snapshot)
            self._enqueue_locked("start", command)

    def stop(self) -> None:
        with self._lock:
            self._ensure_running()
            if not self._snapshot.connected or self._disconnect_requested:
                raise CommandRejected("PLC未连接")
            self._commands = deque((name, value) for name, value in self._commands if name != "start")
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
            with self._lock:
                self._commands.clear()
                self._stop_requested = False
                self._disconnect_requested = False
            if self.snapshot.connected:
                self._client.close()
            self._pending_sync = None
            self._publish(replace(self.snapshot, connected=False))
            return
        if self._disconnect_requested:
            self._process_disconnect()
            return
        if self._stop_requested:
            with self._lock:
                self._stop_requested = False
            self._client.send_stop()
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
            self._validated_start(value.mode, value.direction, value.speed_deg_s, replace(self.snapshot, status=fresh))
            self._attempted_generations.clear()
            self._saved_generations.clear()
            self._handled_generations.clear()
            self._generation_test_ids.clear()
            self._ack_attempts.clear()
            requested_epoch = self._epoch_ms()
            self._run_session = _RunSession(value.mode, value.direction, value.speed_deg_s, requested_epoch)
            self._publish(replace(self.snapshot, status=fresh, active_test_id=f"run_{requested_epoch}", last_error=None))
            start_seq = self._client.send_start(value.mode, value.direction, value.speed_index)
            self._run_session = replace(self._run_session, start_seq=start_seq)
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
            self._worker = Thread(target=self._worker_loop, name="turntable-controller", daemon=True)
            self._worker.start()

    def shutdown(self, timeout: float | None = None) -> None:
        self._shutdown_requested.set()
        self._wake.set()
        worker = self._worker
        if worker is not None:
            worker.join(timeout)
            if worker.is_alive():
                raise ControllerStopped("控制器线程未能在超时内停止")
        else:
            self.process_once()

    def _process_connect(self) -> None:
        self._client.connect()
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
        if self.snapshot.connected:
            self._client.close()
        self._pending_sync = None
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
        pending = self._pending_sync
        if pending is not None and status.time_sync_response_seq == pending[0]:
            self._clock_sync.add_sample(pending[1], status.plc_tick_ms, self._epoch_ms())
            self._pending_sync = None
        session = self._run_session
        terminal = status.run_status in _TERMINAL_RUN_STATUSES
        if session is not None and (status.run_status is RunStatus.RUNNING or terminal):
            if session.start_seq is None or status.start_ack_seq == session.start_seq:
                self._run_session = replace(session, run_start_plc_ms=status.run_start_plc_ms)
        self._publish(replace(self.snapshot, status=status, last_error=None))
        self._attempt_download(status, reconnect=reconnect)

    def _attempt_download(
        self, status: StatusSnapshot, *, explicit: bool = False, reconnect: bool = False
    ) -> None:
        generation = status.event_generation
        buffer_ready = bool(status.status_flags & STATUS_BUFFER_READY)
        if generation in self._handled_generations:
            return
        saved = self._saved_generations.get(generation)
        if saved is not None:
            if not buffer_ready:
                self._handled_generations.add(generation)
                self._publish(replace(self.snapshot, download_pending=False, active_test_id=None))
            elif reconnect or explicit:
                self._acknowledge_generation(generation)
            return
        if not buffer_ready or status.run_status not in _TERMINAL_RUN_STATUSES:
            return
        if generation in self._attempted_generations and not explicit:
            return
        self._attempted_generations.add(generation)
        session = self._run_session
        if session is None or (session.start_seq is not None and status.start_ack_seq != session.start_seq):
            self._download_failed("缺少本次运行会话信息")
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
            path = Path(self._csv_store.save_run(RunExport(metadata=metadata, events=events), self._clock_sync))
        except (CommunicationError, ProtocolMismatch):
            raise
        except Exception as error:
            self._download_failed(str(error))
            return
        self._saved_generations[generation] = path
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
        self._acknowledge_generation(generation)

    def _acknowledge_generation(self, generation: int) -> None:
        key = (self._verified_session, generation)
        if key in self._ack_attempts:
            return
        self._ack_attempts.add(key)
        self._client.acknowledge_buffer()
        self._handled_generations.add(generation)
        self._publish(replace(self.snapshot, download_pending=False, active_test_id=None, last_error=None))

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
        self._pending_sync = None
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
        try:
            while not self._shutdown_requested.is_set():
                self.process_once()
                self._wake.wait(0.05)
                self._wake.clear()
        finally:
            if self.snapshot.connected:
                self._client.close()
                self._publish(replace(self.snapshot, connected=False))
