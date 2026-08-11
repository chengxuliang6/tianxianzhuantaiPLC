from __future__ import annotations

from dataclasses import dataclass
from threading import Thread

import pytest

from turntable_control.domain import Direction, Mode, RunState, RunStatus
from turntable_control.registers import Register, encode_i32, encode_u32


@dataclass
class FakeResponse:
    registers: list[int]
    error: bool = False

    def isError(self) -> bool:
        return self.error


class FakeTransport:
    """Deterministic in-memory transport; it never opens a network socket."""

    def __init__(self) -> None:
        self.memory = [0] * 5000
        self.memory[Register.PROTOCOL_VERSION] = 1
        self.memory[Register.WORD_ORDER_PROBE_HI] = 0x1234
        self.memory[Register.WORD_ORDER_PROBE_LO] = 0x5678
        self.connected = False
        self.connect_result = True
        self.connect_error: Exception | None = None
        self.read_error = False
        self.read_exception: Exception | None = None
        self.short_read = False
        self.read_calls: list[dict[str, int]] = []
        self.write_calls: list[dict[str, object]] = []
        self.close_calls = 0
        self.single_write_error_on_address: int | None = None
        self.single_write_exception_on_address: int | None = None
        self.multiple_write_error_on_call: int | None = None
        self.multiple_write_exception_on_call: int | None = None
        self._multiple_write_calls = 0

    def connect(self) -> bool:
        if self.connect_error:
            raise self.connect_error
        self.connected = self.connect_result
        return self.connect_result

    def close(self) -> None:
        self.close_calls += 1
        self.connected = False

    def read_holding_registers(self, *, address: int, count: int, device_id: int) -> FakeResponse:
        self.read_calls.append({"address": address, "count": count, "device_id": device_id})
        if self.read_exception:
            raise self.read_exception
        values = self.memory[address : address + count]
        return FakeResponse(values[:-1] if self.short_read else values, self.read_error)

    def write_register(self, *, address: int, value: int, device_id: int) -> FakeResponse:
        self.write_calls.append({"kind": "single", "address": address, "value": value, "device_id": device_id})
        if address == self.single_write_exception_on_address:
            raise RuntimeError("single write unavailable")
        self.memory[address] = value
        return FakeResponse([], address == self.single_write_error_on_address)

    def write_registers(self, *, address: int, values: list[int], device_id: int) -> FakeResponse:
        self.write_calls.append({"kind": "multiple", "address": address, "values": values, "device_id": device_id})
        self._multiple_write_calls += 1
        if self._multiple_write_calls == self.multiple_write_exception_on_call:
            raise RuntimeError("multiple write unavailable")
        self.memory[address : address + len(values)] = values
        return FakeResponse([], self._multiple_write_calls == self.multiple_write_error_on_call)


@pytest.fixture
def fake_transport() -> FakeTransport:
    return FakeTransport()


def make_client(fake_transport: FakeTransport, *, device_id: int = 7):
    from turntable_control.modbus_client import TurntableModbusClient

    return TurntableModbusClient(transport=fake_transport, device_id=device_id)


def connect(client) -> None:
    client.connect()


def test_connect_probes_before_enabling_writes_and_uses_keyword_device_id(fake_transport: FakeTransport) -> None:
    client = make_client(fake_transport)
    connect(client)

    assert [call["address"] for call in fake_transport.read_calls[:4]] == [1200, 1201, 1003, 1110]
    assert all(call["device_id"] == 7 for call in fake_transport.read_calls)
    assert fake_transport.write_calls == []


@pytest.mark.parametrize(
    ("address", "value"),
    [(Register.PROTOCOL_VERSION, 2), (Register.WORD_ORDER_PROBE_LO, 0x7856)],
)
def test_connect_rejects_protocol_or_word_order_mismatch_without_writes(
    fake_transport: FakeTransport, address: Register, value: int
) -> None:
    from turntable_control.modbus_client import ProtocolMismatch

    fake_transport.memory[address] = value
    client = make_client(fake_transport)

    with pytest.raises(ProtocolMismatch):
        client.connect()

    assert fake_transport.write_calls == []
    assert fake_transport.close_calls == 1


@pytest.mark.parametrize("failure", ["response_error", "exception", "false_connect", "short_read"])
def test_transport_failures_translate_to_communication_error(fake_transport: FakeTransport, failure: str) -> None:
    from turntable_control.modbus_client import CommunicationError

    if failure == "response_error":
        fake_transport.read_error = True
    elif failure == "exception":
        fake_transport.read_exception = RuntimeError("offline")
    elif failure == "false_connect":
        fake_transport.connect_result = False
    else:
        fake_transport.short_read = True

    with pytest.raises(CommunicationError):
        make_client(fake_transport).connect()


def test_status_decodes_signed_values_raw_u32_and_unknown_enums(fake_transport: FakeTransport) -> None:
    client = make_client(fake_transport)
    connect(client)
    fake_transport.memory[Register.RUN_STATE] = 99
    fake_transport.memory[Register.RUN_STATUS] = RunStatus.RUNNING.value
    fake_transport.memory[Register.ACTUAL_POSITION_HI : Register.ACTUAL_POSITION_LO + 1] = list(encode_i32(-12_345))
    fake_transport.memory[Register.TARGET_POSITION_HI : Register.TARGET_POSITION_LO + 1] = list(encode_i32(12_345))
    fake_transport.memory[Register.ACTUAL_VELOCITY_HI : Register.ACTUAL_VELOCITY_LO + 1] = list(encode_i32(-1_000))
    fake_transport.memory[Register.PLC_TICK_MS_HI : Register.PLC_TICK_MS_LO + 1] = list(encode_u32(0xFFFF_FFFF))

    status = client.read_status()

    assert status.run_state == 99
    assert status.run_status is RunStatus.RUNNING
    assert status.actual_position_deg == -12.345
    assert status.target_position_deg == 12.345
    assert status.actual_velocity_deg_s == -1.0
    assert status.plc_tick_ms == 0xFFFF_FFFF


def test_read_events_chunks_360_records_without_reading_past_fixed_buffer(fake_transport: FakeTransport) -> None:
    client = make_client(fake_transport)
    connect(client)
    for index in range(360):
        base = Register.EVENT_BUFFER_BASE + index * 6
        fake_transport.memory[base : base + 6] = [index + 1, index + 1, *encode_i32(-index), *encode_u32(index)]

    events = client.read_events(360)
    event_reads = [call for call in fake_transport.read_calls if call["address"] >= Register.EVENT_BUFFER_BASE]

    assert len(events) == 360
    assert len(event_reads) == 18
    assert max(call["count"] for call in event_reads) == 120
    assert event_reads[-1]["address"] + event_reads[-1]["count"] - 1 == Register.event_last_address()
    assert events[-1].actual_position_deg == -0.359


def test_event_read_validates_count_and_records_and_zero_needs_no_read(fake_transport: FakeTransport) -> None:
    from turntable_control.modbus_client import CommunicationError

    client = make_client(fake_transport)
    connect(client)
    reads_before = len(fake_transport.read_calls)
    assert client.read_events(0) == []
    assert len(fake_transport.read_calls) == reads_before
    for count in (-1, 361, True, 1.0):
        with pytest.raises(ValueError):
            client.read_events(count)

    fake_transport.memory[Register.EVENT_BUFFER_BASE : Register.EVENT_BUFFER_BASE + 6] = [1, 0, 0, 0, 0, 0]
    with pytest.raises(CommunicationError, match="travel"):
        client.read_events(1)


def test_commands_increment_wrap_and_reconnect_does_not_replay(fake_transport: FakeTransport) -> None:
    client = make_client(fake_transport)
    fake_transport.memory[Register.START_SEQ] = 0xFFFF
    fake_transport.memory[Register.STOP_SEQ] = 0xFFFF
    connect(client)

    assert client.send_start(Mode.MANUAL, Direction.CW, 1) == 0
    assert client.send_stop() == 0
    writes_before_reconnect = list(fake_transport.write_calls)
    reads_before_reconnect = len(fake_transport.read_calls)
    client.reconnect()

    assert fake_transport.write_calls == writes_before_reconnect
    assert any(call["address"] == Register.RUN_STATE for call in fake_transport.read_calls[reads_before_reconnect:])


def test_start_writes_fixed_parameters_then_settings_then_sequence_and_rejects_invalid_input(
    fake_transport: FakeTransport,
) -> None:
    client = make_client(fake_transport)
    connect(client)

    client.send_start(Mode.AUTO, Direction.CCW, 5)
    assert [call["address"] for call in fake_transport.write_calls] == [1010, 1012, 1014, 1016, 1018, 1000, 1001, 1002, 1003]
    assert fake_transport.write_calls[-1] == {"kind": "single", "address": 1003, "value": 1, "device_id": 7}

    writes_before = list(fake_transport.write_calls)
    for bad_args in [(0, Direction.CW, 1), (Mode.AUTO, -1, 1), (Mode.AUTO, Direction.CW, 0), (Mode.AUTO, Direction.CW, 6)]:
        with pytest.raises(ValueError):
            client.send_start(*bad_args)
    assert fake_transport.write_calls == writes_before


def test_start_refuses_parameter_write_while_run_status_is_running(fake_transport: FakeTransport) -> None:
    from turntable_control.modbus_client import CommunicationError

    client = make_client(fake_transport)
    connect(client)
    fake_transport.memory[Register.RUN_STATUS] = RunStatus.RUNNING.value

    with pytest.raises(CommunicationError, match="running"):
        client.send_start(Mode.AUTO, Direction.CW, 1)
    assert fake_transport.write_calls == []


def test_stop_is_one_direct_write_despite_unacknowledged_start(fake_transport: FakeTransport) -> None:
    client = make_client(fake_transport)
    connect(client)
    client.send_start(Mode.AUTO, Direction.CW, 1)
    writes_before_stop = len(fake_transport.write_calls)

    assert client.send_stop() == 1
    assert fake_transport.write_calls[writes_before_stop:] == [
        {"kind": "single", "address": 1004, "value": 1, "device_id": 7}
    ]


def test_small_commands_heartbeat_and_time_sync_use_raw_sequences(fake_transport: FakeTransport) -> None:
    client = make_client(fake_transport)
    fake_transport.memory[Register.SET_ZERO_SEQ] = 5
    fake_transport.memory[Register.RESET_FAULT_SEQ] = 6
    fake_transport.memory[Register.POWER_SEQ] = 7
    fake_transport.memory[Register.BUFFER_ACK_SEQ] = 8
    fake_transport.memory[Register.TIME_SYNC_REQUEST_SEQ] = 9
    connect(client)

    assert [client.set_zero(), client.reset_alarm(), client.toggle_power(), client.acknowledge_buffer(), client.request_time_sync()] == [6, 7, 8, 9, 10]
    client.write_heartbeat(0xFFFF)
    with pytest.raises(ValueError):
        client.write_heartbeat(True)
    assert fake_transport.write_calls[-1] == {"kind": "single", "address": 1008, "value": 0xFFFF, "device_id": 7}


def test_close_is_idempotent_and_sequence_increments_are_thread_safe(fake_transport: FakeTransport) -> None:
    client = make_client(fake_transport)
    connect(client)
    results: list[int] = []
    threads = [Thread(target=lambda: results.append(client.send_stop())) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == list(range(1, 9))
    client.close()
    client.close()
    assert fake_transport.close_calls == 1


@pytest.mark.parametrize("failure", ["error", "exception"])
def test_ambiguous_single_write_invalidates_session_and_requires_reconnect(
    fake_transport: FakeTransport, failure: str
) -> None:
    from turntable_control.modbus_client import CommunicationError, ProtocolMismatch

    client = make_client(fake_transport)
    connect(client)
    if failure == "error":
        fake_transport.single_write_error_on_address = Register.STOP_SEQ
    else:
        fake_transport.single_write_exception_on_address = Register.STOP_SEQ

    with pytest.raises(CommunicationError, match="stop sequence"):
        client.send_stop()
    with pytest.raises(ProtocolMismatch):
        client.send_stop()
    assert fake_transport.close_calls == 1
    writes_before_reconnect = list(fake_transport.write_calls)
    client.reconnect()
    assert fake_transport.write_calls == writes_before_reconnect


@pytest.mark.parametrize("failure", ["error", "exception"])
def test_ambiguous_multiple_write_invalidates_session(fake_transport: FakeTransport, failure: str) -> None:
    from turntable_control.modbus_client import CommunicationError, ProtocolMismatch

    client = make_client(fake_transport)
    connect(client)
    if failure == "error":
        fake_transport.multiple_write_error_on_call = 1
    else:
        fake_transport.multiple_write_exception_on_call = 1

    with pytest.raises(CommunicationError, match="start parameter"):
        client.send_start(Mode.AUTO, Direction.CW, 1)
    with pytest.raises(ProtocolMismatch):
        client.send_stop()


def test_parameter_write_failure_never_reaches_start_sequence(fake_transport: FakeTransport) -> None:
    from turntable_control.modbus_client import CommunicationError, ProtocolMismatch

    client = make_client(fake_transport)
    connect(client)
    fake_transport.multiple_write_error_on_call = 3

    with pytest.raises(CommunicationError, match="start parameter"):
        client.send_start(Mode.AUTO, Direction.CW, 1)
    assert all(call["address"] != Register.START_SEQ for call in fake_transport.write_calls)
    with pytest.raises(ProtocolMismatch):
        client.send_stop()


def test_start_response_error_is_ambiguous_and_disables_following_commands(fake_transport: FakeTransport) -> None:
    from turntable_control.modbus_client import CommunicationError, ProtocolMismatch

    client = make_client(fake_transport)
    connect(client)
    fake_transport.single_write_error_on_address = Register.START_SEQ

    with pytest.raises(CommunicationError, match="start sequence"):
        client.send_start(Mode.AUTO, Direction.CW, 1)
    assert fake_transport.memory[Register.START_SEQ] == 1
    with pytest.raises(ProtocolMismatch):
        client.send_stop()
    writes_before_reconnect = list(fake_transport.write_calls)
    client.reconnect()
    assert fake_transport.write_calls == writes_before_reconnect


@pytest.mark.parametrize("failure", ["response_error", "exception", "short_read", "malformed"])
def test_verified_read_failures_invalidate_session(fake_transport: FakeTransport, failure: str) -> None:
    from turntable_control.modbus_client import CommunicationError, ProtocolMismatch

    client = make_client(fake_transport)
    connect(client)
    if failure == "response_error":
        fake_transport.read_error = True
    elif failure == "exception":
        fake_transport.read_exception = RuntimeError("read unavailable")
    elif failure == "short_read":
        fake_transport.short_read = True
    else:
        fake_transport.memory[Register.RUN_STATE] = -1

    with pytest.raises(CommunicationError):
        client.read_status()
    with pytest.raises(ProtocolMismatch):
        client.send_stop()
    assert fake_transport.close_calls == 1


@pytest.mark.parametrize(
    ("address", "value"),
    [(Register.PROTOCOL_VERSION, 2), (Register.WORD_ORDER_PROBE_HI, 0x3412)],
)
def test_status_rechecks_protocol_probe_and_invalidates_on_mismatch(
    fake_transport: FakeTransport, address: Register, value: int
) -> None:
    from turntable_control.modbus_client import ProtocolMismatch

    client = make_client(fake_transport)
    connect(client)
    fake_transport.memory[address] = value

    with pytest.raises(ProtocolMismatch):
        client.read_status()
    with pytest.raises(ProtocolMismatch):
        client.send_stop()
    assert fake_transport.close_calls == 1


def test_event_sequences_must_match_the_record_prefix(fake_transport: FakeTransport) -> None:
    from turntable_control.modbus_client import CommunicationError

    client = make_client(fake_transport)
    connect(client)
    fake_transport.memory[Register.EVENT_BUFFER_BASE : Register.EVENT_BUFFER_BASE + 12] = [1, 1, 0, 0, 0, 0, 1, 2, 0, 0, 0, 0]

    with pytest.raises(CommunicationError, match="sequence"):
        client.read_events(2)


def test_repeated_connect_closes_the_previous_uncertain_session(fake_transport: FakeTransport) -> None:
    client = make_client(fake_transport)
    connect(client)

    client.connect()

    assert fake_transport.close_calls == 1
