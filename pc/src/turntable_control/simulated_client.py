"""Thread-safe, network-free client adapter for deterministic demonstrations."""

from __future__ import annotations

from collections.abc import Callable
from threading import RLock

from .controller import (
    STATUS_BUFFER_READY,
    STATUS_HEARTBEAT_OK,
    STATUS_POWERED,
    STATUS_SOFT_LIMIT,
    STATUS_ZERO_VALID,
)
from .domain import (
    Direction,
    Mode,
    MotionRejected,
    RunState,
    RunStatus,
    SPEEDS_DEG_S,
    SOFT_MAX_DEG,
    SOFT_MIN_DEG,
)
from .modbus_client import CommunicationError, EventRecord, StatusSnapshot
from .simulator import TurntableSimulator


_U16_MASK = 0xFFFF
_U32_MASK = 0xFFFF_FFFF


class SimulatedTurntableClient:
    """Expose the production client contract without sockets or PLC access."""

    def __init__(
        self,
        *,
        monotonic_ms: Callable[[], int],
        initial_plc_tick_ms: int = 0,
        initial_position_deg: float = 0.0,
    ) -> None:
        if not callable(monotonic_ms):
            raise TypeError("monotonic_ms must be callable")
        if type(initial_plc_tick_ms) is not int or not 0 <= initial_plc_tick_ms <= _U32_MASK:
            raise ValueError("initial_plc_tick_ms must be an unsigned 32-bit integer")
        self._clock = monotonic_ms
        now = self._read_clock()
        self._last_monotonic_ms = now
        self._initial_monotonic_ms = now
        self._initial_plc_tick_ms = initial_plc_tick_ms
        self._simulator = TurntableSimulator(position_deg=initial_position_deg)
        self._lock = RLock()
        self._connected = False
        self._zero_valid = False
        self._powered = False
        self._heartbeat_echo = 0
        self._heartbeat_age_ms = 0
        self._event_generation = 0
        self._run_start_plc_ms = initial_plc_tick_ms
        self._time_sync_request_seq = 0
        self._time_sync_response_seq = 0
        self._sequences = {
            "start": 0,
            "stop": 0,
            "zero": 0,
            "reset": 0,
            "power": 0,
            "buffer": 0,
        }
        self._acks = dict(self._sequences)

    @property
    def start_command_seq(self) -> int:
        with self._lock:
            self._require_connected()
            return self._sequences["start"]

    def connect(self) -> None:
        with self._lock:
            self._advance()
            self._connected = True

    def close(self) -> None:
        with self._lock:
            self._advance()
            self._connected = False

    def reconnect(self) -> None:
        with self._lock:
            self.close()
            self.connect()

    def read_status(self) -> StatusSnapshot:
        with self._lock:
            self._require_connected()
            self._advance()
            flags = 0
            if self._zero_valid:
                flags |= STATUS_ZERO_VALID
            if self._powered:
                flags |= STATUS_POWERED
            if self._buffer_ready:
                flags |= STATUS_BUFFER_READY
            if self._simulator.position_deg <= SOFT_MIN_DEG or self._simulator.position_deg >= SOFT_MAX_DEG:
                flags |= STATUS_SOFT_LIMIT
            if self._heartbeat_age_ms <= 1000:
                flags |= STATUS_HEARTBEAT_OK
            run_state = self._simulator.run_state if self._zero_valid else RunState.ZERO_REQUIRED
            return StatusSnapshot(
                run_state=run_state,
                status_flags=flags,
                fault_code=0,
                actual_position_deg=self._simulator.position_deg,
                target_position_deg=self._simulator.target_deg,
                actual_velocity_deg_s=self._simulator.velocity_deg_s,
                heartbeat_echo=self._heartbeat_echo,
                start_ack_seq=self._acks["start"],
                stop_ack_seq=self._acks["stop"],
                set_zero_ack_seq=self._acks["zero"],
                reset_fault_ack_seq=self._acks["reset"],
                power_ack_seq=self._acks["power"],
                buffer_acked_seq=self._acks["buffer"],
                event_count=len(self._simulator.events),
                event_generation=self._event_generation,
                run_status=self._simulator.run_status,
                run_start_plc_ms=self._run_start_plc_ms,
                protocol_version=2,
                word_order_probe=0x12345678,
                time_sync_request_seq=self._time_sync_request_seq,
                plc_tick_ms=self._plc_tick_ms(),
                time_sync_response_seq=self._time_sync_response_seq,
            )

    def read_events(self, count: int) -> list[EventRecord]:
        with self._lock:
            self._require_connected()
            self._advance()
            if type(count) is not int or not 0 <= count <= 360:
                raise ValueError("count must be an integer in 0..360")
            if count > len(self._simulator.events):
                raise CommunicationError("requested event prefix is not available")
            return [
                EventRecord(
                    sequence=event.sequence,
                    travel_angle_deg=event.travel_angle_deg,
                    actual_position_deg=event.actual_position_deg,
                    elapsed_ms=event.elapsed_ms,
                )
                for event in self._simulator.events[:count]
            ]

    def send_start(self, mode: Mode, direction: Direction, speed_index: int) -> int:
        with self._lock:
            self._require_connected()
            self._advance()
            if type(mode) is not Mode:
                raise ValueError("mode must be a Mode enum member")
            if type(direction) is not Direction:
                raise ValueError("direction must be a Direction enum member")
            if type(speed_index) is not int or not 1 <= speed_index <= len(SPEEDS_DEG_S):
                raise ValueError("speed_index must be an integer in 1..5")
            if not self._zero_valid:
                raise MotionRejected("未设零，禁止启动")
            if not self._powered:
                raise MotionRejected("伺服未上电，禁止启动")
            if self._simulator.run_status is RunStatus.RUNNING or self._simulator.run_state is RunState.STOPPING:
                raise MotionRejected("转台正在运行")
            if self._buffer_ready:
                raise MotionRejected("逐度数据尚未保存确认")
            self._simulator.start(mode, direction, SPEEDS_DEG_S[speed_index - 1])
            sequence = self._increment("start")
            self._acks["start"] = sequence
            self._event_generation = (self._event_generation + 1) & _U16_MASK
            self._run_start_plc_ms = self._plc_tick_ms()
            return sequence

    def send_stop(self) -> int:
        with self._lock:
            self._require_connected()
            self._advance()
            sequence = self._increment("stop")
            self._simulator.request_stop()
            self._acks["stop"] = sequence
            return sequence

    def set_zero(self) -> int:
        with self._lock:
            self._require_connected()
            self._advance()
            if self._simulator.run_state in (RunState.MANUAL_RUNNING, RunState.AUTO_RUNNING, RunState.STOPPING):
                raise MotionRejected("运行中禁止设零")
            self._simulator.position_deg = 0.0
            self._simulator.target_deg = 0.0
            self._zero_valid = True
            sequence = self._increment("zero")
            self._acks["zero"] = sequence
            return sequence

    def reset_alarm(self) -> int:
        with self._lock:
            self._require_connected()
            self._advance()
            sequence = self._increment("reset")
            self._acks["reset"] = sequence
            return sequence

    def toggle_power(self) -> int:
        with self._lock:
            self._require_connected()
            self._advance()
            if self._simulator.run_state in (RunState.MANUAL_RUNNING, RunState.AUTO_RUNNING, RunState.STOPPING):
                raise MotionRejected("运行中禁止切换伺服上电")
            self._powered = not self._powered
            sequence = self._increment("power")
            self._acks["power"] = sequence
            return sequence

    def write_heartbeat(self, value: int) -> None:
        with self._lock:
            self._require_connected()
            if type(value) is not int or not 0 <= value <= _U16_MASK:
                raise ValueError("heartbeat must be an unsigned 16-bit integer")
            self._advance()
            self._heartbeat_echo = value
            self._heartbeat_age_ms = 0
            self._simulator.receive_heartbeat()

    def acknowledge_buffer(self) -> int:
        with self._lock:
            self._require_connected()
            self._advance()
            self._simulator.acknowledge_events()
            sequence = self._increment("buffer")
            self._acks["buffer"] = sequence
            return sequence

    def request_time_sync(self) -> int:
        with self._lock:
            self._require_connected()
            self._advance()
            self._time_sync_request_seq = (self._time_sync_request_seq + 1) & _U16_MASK
            self._time_sync_response_seq = self._time_sync_request_seq
            return self._time_sync_request_seq

    def _advance(self) -> None:
        now = self._read_clock()
        delta = now - self._last_monotonic_ms
        if delta < 0:
            raise CommunicationError("monotonic clock moved backwards")
        if delta:
            self._simulator.tick(delta, heartbeat_updated=False)
            self._heartbeat_age_ms += delta
            self._last_monotonic_ms = now

    def _plc_tick_ms(self) -> int:
        elapsed = self._read_clock() - self._initial_monotonic_ms
        return (self._initial_plc_tick_ms + elapsed) & _U32_MASK

    def _read_clock(self) -> int:
        value = self._clock()
        if type(value) is not int:
            raise TypeError("monotonic_ms must return an integer")
        return value

    def _require_connected(self) -> None:
        if not self._connected:
            raise CommunicationError("simulator client is not connected")

    def _increment(self, name: str) -> int:
        value = (self._sequences[name] + 1) & _U16_MASK
        self._sequences[name] = value
        return value

    @property
    def _buffer_ready(self) -> bool:
        return self._simulator.buffer_pending and self._simulator.run_state in (
            RunState.READY,
            RunState.FAULT,
        )
