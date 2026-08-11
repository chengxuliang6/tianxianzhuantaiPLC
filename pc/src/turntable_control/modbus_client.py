"""Safe synchronous Modbus TCP client for the turntable PLC contract.

The client deliberately separates connection/protocol verification from writes.
It is a software communication layer; a ``send_stop`` call is a software stop,
not a physical emergency-stop function.
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Any, Protocol, Sequence

from pymodbus.client import ModbusTcpClient

from .domain import Direction, Mode, RunState, RunStatus
from .registers import EVENT_RECORD_COUNT, EVENT_RECORD_WORDS, Register, decode_i32, decode_u32, encode_i32


class CommunicationError(RuntimeError):
    """The Modbus transport could not complete the requested operation."""


class ProtocolMismatch(CommunicationError):
    """The connected PLC does not implement the expected register contract."""


@dataclass(frozen=True)
class StatusSnapshot:
    run_state: RunState | int
    status_flags: int
    fault_code: int
    actual_position_deg: float
    target_position_deg: float
    actual_velocity_deg_s: float
    heartbeat_echo: int
    start_ack_seq: int
    stop_ack_seq: int
    set_zero_ack_seq: int
    reset_fault_ack_seq: int
    power_ack_seq: int
    buffer_acked_seq: int
    event_count: int
    event_generation: int
    run_status: RunStatus | int
    protocol_version: int
    word_order_probe: int
    time_sync_request_seq: int
    plc_tick_ms: int
    time_sync_response_seq: int


@dataclass(frozen=True)
class EventRecord:
    sequence: int
    travel_angle_deg: int
    actual_position_deg: float
    elapsed_ms: int


class _Transport(Protocol):
    def connect(self) -> bool: ...

    def close(self) -> None: ...

    def read_holding_registers(self, *, address: int, count: int, device_id: int) -> Any: ...

    def write_register(self, *, address: int, value: int, device_id: int) -> Any: ...

    def write_registers(self, *, address: int, values: list[int], device_id: int) -> Any: ...


class _PymodbusTransport:
    """Small adapter around pymodbus 3.14's synchronous TCP client."""

    def __init__(self, host: str | None, port: int, timeout: float) -> None:
        self._client = ModbusTcpClient(host=host or "127.0.0.1", port=port, timeout=timeout)

    def connect(self) -> bool:
        return self._client.connect()

    def close(self) -> None:
        self._client.close()

    def read_holding_registers(self, *, address: int, count: int, device_id: int) -> Any:
        return self._client.read_holding_registers(address=address, count=count, device_id=device_id)

    def write_register(self, *, address: int, value: int, device_id: int) -> Any:
        return self._client.write_register(address=address, value=value, device_id=device_id)

    def write_registers(self, *, address: int, values: list[int], device_id: int) -> Any:
        return self._client.write_registers(address=address, values=values, device_id=device_id)


_PARAMETER_DEFAULTS: tuple[tuple[Register, int], ...] = (
    (Register.TOTAL_RATIO_HI, 50_000),
    (Register.ACCELERATION_HI, 5_000),
    (Register.DECELERATION_HI, 5_000),
    (Register.SOFTWARE_STOP_DECELERATION_HI, 10_000),
    (Register.BACKLASH_COMPENSATION_HI, 0),
)

_SEQUENCE_REGISTERS: tuple[Register, ...] = (
    Register.START_SEQ,
    Register.STOP_SEQ,
    Register.SET_ZERO_SEQ,
    Register.RESET_FAULT_SEQ,
    Register.POWER_SEQ,
    Register.BUFFER_ACK_SEQ,
)


class TurntableModbusClient:
    """Protocol-verified, lock-protected client using 0-based PLC D addresses."""

    def __init__(
        self,
        host: str | None = None,
        port: int = 502,
        device_id: int = 1,
        timeout: float = 1.0,
        transport: _Transport | None = None,
    ) -> None:
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("port must be an integer in 1..65535")
        if type(device_id) is not int or not 0 <= device_id <= 247:
            raise ValueError("device_id must be an integer in 0..247")
        if type(timeout) not in (int, float) or isinstance(timeout, bool) or timeout <= 0:
            raise ValueError("timeout must be positive")
        self.host = host
        self.port = port
        self.device_id = device_id
        self.timeout = float(timeout)
        self._transport: _Transport = transport or _PymodbusTransport(host, port, self.timeout)
        self._lock = RLock()
        self._transport_open = False
        self._verified = False
        self._sequences: dict[Register, int] = {}
        self._time_sync_sequence = 0

    def connect(self) -> None:
        """Open a session and perform the required read-only protocol probe."""
        with self._lock:
            self._verified = False
            try:
                connected = self._transport.connect()
            except Exception as error:
                raise CommunicationError(f"connect: {error}") from error
            if connected is not True:
                raise CommunicationError("connect: transport returned false")
            self._transport_open = True
            try:
                version = self._read_words(Register.PROTOCOL_VERSION, 1, "protocol version")[0]
                if version != 1:
                    raise ProtocolMismatch(f"protocol version at D1200 is {version}, expected 1")
                probe = self._read_words(Register.WORD_ORDER_PROBE_HI, 2, "word-order probe")
                if probe != [0x1234, 0x5678]:
                    raise ProtocolMismatch("word-order probe at D1201:D1202 is not 0x1234,0x5678")
                command_words = self._read_words(Register.START_SEQ, 7, "command sequence words")
                ack_words = self._read_words(Register.START_ACK_SEQ, 6, "acknowledgement words")
                self._sequences = {
                    register: command_words[int(register) - int(Register.START_SEQ)]
                    for register in _SEQUENCE_REGISTERS
                }
                # Read acknowledgements to refresh the session view without replaying anything.
                _ = ack_words
                self._time_sync_sequence = self._read_words(
                    Register.TIME_SYNC_REQUEST_SEQ, 1, "time-sync sequence"
                )[0]
                self._verified = True
            except ProtocolMismatch:
                self._invalidate_session()
                raise
            except CommunicationError:
                self._invalidate_session()
                raise

    def close(self) -> None:
        """Close the transport once; repeated closes are safe."""
        with self._lock:
            self._verified = False
            self._sequences.clear()
            if not self._transport_open:
                return
            try:
                self._transport.close()
            except Exception as error:
                raise CommunicationError(f"close: {error}") from error
            finally:
                self._transport_open = False

    def reconnect(self) -> None:
        """Refresh read-only session state without replaying pending commands."""
        with self._lock:
            self.close()
            self.connect()

    def read_status(self) -> StatusSnapshot:
        with self._lock:
            self._require_verified()
            status = self._read_words(Register.RUN_STATE, 19, "status")
            protocol = self._read_words(Register.PROTOCOL_VERSION, 7, "protocol status")
            return StatusSnapshot(
                run_state=_enum_or_raw(RunState, status[0]),
                status_flags=status[1],
                fault_code=status[2],
                actual_position_deg=_decode_scaled_i32(status[3:5], "actual position"),
                target_position_deg=_decode_scaled_i32(status[5:7], "target position"),
                actual_velocity_deg_s=_decode_scaled_i32(status[7:9], "actual velocity"),
                heartbeat_echo=status[9],
                start_ack_seq=status[10],
                stop_ack_seq=status[11],
                set_zero_ack_seq=status[12],
                reset_fault_ack_seq=status[13],
                power_ack_seq=status[14],
                buffer_acked_seq=status[15],
                event_count=status[16],
                event_generation=status[17],
                run_status=_enum_or_raw(RunStatus, status[18]),
                protocol_version=protocol[0],
                word_order_probe=_decode_i32(protocol[1:3], "word-order probe"),
                time_sync_request_seq=protocol[3],
                plc_tick_ms=_decode_u32(protocol[4:6], "PLC tick"),
                time_sync_response_seq=protocol[6],
            )

    def read_events(self, count: int) -> list[EventRecord]:
        if type(count) is not int or not 0 <= count <= EVENT_RECORD_COUNT:
            raise ValueError("count must be an integer in 0..360")
        with self._lock:
            self._require_verified()
            if count == 0:
                return []
            total_words = count * EVENT_RECORD_WORDS
            words: list[int] = []
            address = int(Register.EVENT_BUFFER_BASE)
            remaining = total_words
            while remaining:
                chunk_size = min(120, remaining)
                words.extend(self._read_words(address, chunk_size, "event buffer"))
                address += chunk_size
                remaining -= chunk_size
            if address - 1 > Register.event_last_address():
                raise CommunicationError("event buffer request crosses D4159")
            return [self._decode_event(words[index : index + EVENT_RECORD_WORDS]) for index in range(0, total_words, 6)]

    def send_start(self, mode: Mode, direction: Direction, speed_index: int) -> int:
        if type(mode) is not Mode:
            raise ValueError("mode must be a Mode enum member")
        if type(direction) is not Direction:
            raise ValueError("direction must be a Direction enum member")
        if type(speed_index) is not int or not 1 <= speed_index <= 5:
            raise ValueError("speed_index must be an integer in 1..5")
        with self._lock:
            self._require_verified()
            if self.read_status().run_status is RunStatus.RUNNING:
                raise CommunicationError("cannot write start parameters while run status is running")
            for register, value in _PARAMETER_DEFAULTS:
                self._write_registers(register, list(encode_i32(value)), "start parameter")
            self._write_register(Register.MODE, int(mode), "mode")
            self._write_register(Register.DIRECTION, int(direction) & 0xFFFF, "direction")
            self._write_register(Register.SPEED_INDEX, speed_index, "speed index")
            return self._increment_sequence(Register.START_SEQ, "start sequence")

    def send_stop(self) -> int:
        """Issue one direct software-stop sequence write; this is not an emergency stop."""
        with self._lock:
            self._require_verified()
            return self._increment_sequence(Register.STOP_SEQ, "stop sequence")

    def set_zero(self) -> int:
        with self._lock:
            self._require_verified()
            return self._increment_sequence(Register.SET_ZERO_SEQ, "set-zero sequence")

    def reset_alarm(self) -> int:
        with self._lock:
            self._require_verified()
            return self._increment_sequence(Register.RESET_FAULT_SEQ, "reset-fault sequence")

    def toggle_power(self) -> int:
        with self._lock:
            self._require_verified()
            return self._increment_sequence(Register.POWER_SEQ, "power sequence")

    def write_heartbeat(self, value: int) -> None:
        if type(value) is not int or not 0 <= value <= 0xFFFF:
            raise ValueError("heartbeat must be an unsigned 16-bit integer")
        with self._lock:
            self._require_verified()
            self._write_register(Register.HEARTBEAT, value, "heartbeat")

    def acknowledge_buffer(self) -> int:
        with self._lock:
            self._require_verified()
            return self._increment_sequence(Register.BUFFER_ACK_SEQ, "buffer acknowledgement sequence")

    def request_time_sync(self) -> int:
        with self._lock:
            self._require_verified()
            self._time_sync_sequence = (self._time_sync_sequence + 1) & 0xFFFF
            self._write_register(Register.TIME_SYNC_REQUEST_SEQ, self._time_sync_sequence, "time-sync sequence")
            return self._time_sync_sequence

    def _require_verified(self) -> None:
        if not self._verified:
            raise ProtocolMismatch("writes and reads are disabled until protocol verification succeeds")

    def _invalidate_session(self) -> None:
        self._verified = False
        self._sequences.clear()
        if self._transport_open:
            try:
                self._transport.close()
            except Exception:
                pass
            self._transport_open = False

    def _read_words(self, address: int | Register, count: int, operation: str) -> list[int]:
        try:
            response = self._transport.read_holding_registers(
                address=int(address), count=count, device_id=self.device_id
            )
            if response is None or response.isError():
                raise CommunicationError(f"{operation} at D{int(address)}: Modbus error response")
            registers = response.registers
        except CommunicationError:
            raise
        except Exception as error:
            raise CommunicationError(f"{operation} at D{int(address)}: {error}") from error
        if not isinstance(registers, Sequence) or len(registers) != count:
            actual = len(registers) if isinstance(registers, Sequence) else "missing"
            raise CommunicationError(f"{operation} at D{int(address)}: expected {count} registers, got {actual}")
        if any(type(word) is not int or not 0 <= word <= 0xFFFF for word in registers):
            raise CommunicationError(f"{operation} at D{int(address)}: malformed register data")
        return list(registers)

    def _write_register(self, address: Register, value: int, operation: str) -> None:
        try:
            response = self._transport.write_register(address=int(address), value=value, device_id=self.device_id)
            if response is None or response.isError():
                raise CommunicationError(f"{operation} at D{int(address)}: Modbus error response")
        except CommunicationError:
            raise
        except Exception as error:
            raise CommunicationError(f"{operation} at D{int(address)}: {error}") from error

    def _write_registers(self, address: Register, values: list[int], operation: str) -> None:
        try:
            response = self._transport.write_registers(address=int(address), values=values, device_id=self.device_id)
            if response is None or response.isError():
                raise CommunicationError(f"{operation} at D{int(address)}: Modbus error response")
        except CommunicationError:
            raise
        except Exception as error:
            raise CommunicationError(f"{operation} at D{int(address)}: {error}") from error

    def _increment_sequence(self, address: Register, operation: str) -> int:
        value = (self._sequences[address] + 1) & 0xFFFF
        self._write_register(address, value, operation)
        self._sequences[address] = value
        return value

    @staticmethod
    def _decode_event(words: Sequence[int]) -> EventRecord:
        if len(words) != EVENT_RECORD_WORDS:
            raise CommunicationError("event record has an invalid word count")
        sequence, travel_angle = words[:2]
        if not 0 <= sequence <= 0xFFFF:
            raise CommunicationError("event sequence is outside u16 range")
        if not 1 <= travel_angle <= EVENT_RECORD_COUNT:
            raise CommunicationError("event travel angle is outside 1..360")
        return EventRecord(
            sequence=sequence,
            travel_angle_deg=travel_angle,
            actual_position_deg=_decode_scaled_i32(words[2:4], "event position"),
            elapsed_ms=_decode_u32(words[4:6], "event elapsed time"),
        )


def _enum_or_raw(enum_type: type[RunState] | type[RunStatus], value: int) -> RunState | RunStatus | int:
    try:
        return enum_type(value)
    except ValueError:
        return value


def _decode_i32(words: Sequence[int], field: str) -> int:
    try:
        return decode_i32(words)
    except ValueError as error:
        raise CommunicationError(f"{field}: {error}") from error


def _decode_u32(words: Sequence[int], field: str) -> int:
    try:
        return decode_u32(words)
    except ValueError as error:
        raise CommunicationError(f"{field}: {error}") from error


def _decode_scaled_i32(words: Sequence[int], field: str) -> float:
    return _decode_i32(words, field) / 1000.0
