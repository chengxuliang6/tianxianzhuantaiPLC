from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import pytest

from turntable_control.controller import (
    CommandRejected,
    STATUS_BUFFER_READY,
    STATUS_SOFT_LIMIT,
    TurntableController,
)
from turntable_control.csv_store import CsvSaveError, CsvStore
from turntable_control.domain import Direction, Mode, RunState, RunStatus
from turntable_control.simulated_client import SimulatedTurntableClient
from turntable_control.time_sync import ClockSynchronizer


@dataclass
class SimulationClock:
    monotonic: int = 0
    epoch_base: int = 1_800_000_000_000

    @property
    def epoch(self) -> int:
        return self.epoch_base + self.monotonic

    def advance(self, milliseconds: int) -> None:
        self.monotonic += milliseconds


class SimulatedSystem:
    def __init__(self, root: Path, *, initial_plc_tick_ms: int = 0) -> None:
        self.clock = SimulationClock()
        self.client = SimulatedTurntableClient(
            monotonic_ms=lambda: self.clock.monotonic,
            initial_plc_tick_ms=initial_plc_tick_ms,
        )
        self.controller = TurntableController(
            self.client,
            CsvStore(root),
            ClockSynchronizer(),
            epoch_ms=lambda: self.clock.epoch,
            monotonic_ms=lambda: self.clock.monotonic,
        )

    def process(self, *, advance_ms: int = 0) -> None:
        if advance_ms:
            self.clock.advance(advance_ms)
        self.controller.process_once()

    def commission(self) -> None:
        self.controller.connect()
        self.process()
        self.controller.set_zero()
        self.process()
        self.controller.toggle_power()
        self.process()
        self.process(advance_ms=100)
        status = self.controller.snapshot.status
        assert status is not None and status.run_state is RunState.READY

    def run_until_saved(self, *, step_ms: int = 100, limit: int = 5_000) -> Path:
        for _ in range(limit):
            self.process(advance_ms=step_ms)
            snapshot = self.controller.snapshot
            status = snapshot.status
            if (
                snapshot.saved_csv is not None
                and not snapshot.download_pending
                and status is not None
                and not status.status_flags & STATUS_BUFFER_READY
            ):
                return snapshot.saved_csv
        raise AssertionError("simulated run did not save and acknowledge in time")


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


@pytest.mark.parametrize("direction", [Direction.CW, Direction.CCW])
@pytest.mark.parametrize("speed", [1.0, 2.0, 4.0, 5.0, 10.0])
def test_all_automatic_speed_direction_runs_export_exact_degree_csv(
    tmp_path: Path, direction: Direction, speed: float
) -> None:
    system = SimulatedSystem(tmp_path / f"{direction.name}_{speed:g}")
    system.commission()

    started_at = system.clock.monotonic
    system.controller.start(Mode.AUTO, direction, speed)
    system.process()
    path = system.run_until_saved()
    status = system.controller.snapshot.status
    assert status is not None
    assert status.run_state is RunState.READY
    assert status.run_status is RunStatus.COMPLETED
    assert status.actual_position_deg == pytest.approx(360.0 * direction.value)
    assert status.status_flags & STATUS_SOFT_LIMIT

    rows = read_rows(path)
    assert len(rows) == 360
    assert [int(row["sequence"]) for row in rows] == list(range(1, 361))
    assert [int(row["travel_angle_deg"]) for row in rows] == list(range(1, 361))
    assert [float(row["actual_position_deg"]) for row in rows] == pytest.approx(
        [float(angle * direction.value) for angle in range(1, 361)]
    )
    assert {row["mode"] for row in rows} == {"AUTO"}
    assert {row["direction"] for row in rows} == {direction.name}
    assert {float(row["speed_deg_s"]) for row in rows} == {speed}
    assert {row["run_status"] for row in rows} == {"COMPLETED"}
    timestamps = [int(row["epoch_ms"]) for row in rows]
    assert timestamps == sorted(timestamps)
    duration_ms = int(rows[-1]["elapsed_ms"])
    assert duration_ms > 360_000 / speed
    assert duration_ms < 360_000 / speed + 5_000
    assert system.clock.monotonic - started_at < 500_000


def test_controller_simulator_complete_commissioning_flow_saves_before_ack(tmp_path: Path) -> None:
    system = SimulatedSystem(tmp_path)
    system.commission()
    system.controller.start(Mode.AUTO, Direction.CW, 5.0)
    system.process()

    path = system.run_until_saved()
    assert path.exists()
    assert len(read_rows(path)) == 360
    assert system.client.read_status().event_count == 0
    assert system.controller.snapshot.active_test_id is None


def test_main_simulator_mode_never_constructs_network_client(monkeypatch, tmp_path: Path) -> None:
    from turntable_control import main as app_main

    captured: dict[str, object] = {}

    class FakeApplication:
        @staticmethod
        def instance():
            return None

        def __init__(self, _argv) -> None:
            pass

        def setApplicationName(self, _name: str) -> None:
            pass

        def exec(self) -> int:
            return 0

    class FakeWindow:
        def __init__(self, factory, data_dir, **options) -> None:
            captured.update(factory=factory, data_dir=Path(data_dir), options=options)

        def show(self) -> None:
            pass

    class ForbiddenNetworkClient:
        def __init__(self, *args, **kwargs) -> None:
            raise AssertionError("simulator mode constructed a network client")

    monkeypatch.setattr(app_main, "QApplication", FakeApplication)
    monkeypatch.setattr(app_main, "MainWindow", FakeWindow)
    monkeypatch.setattr(app_main, "TurntableModbusClient", ForbiddenNetworkClient)

    assert app_main.main(["--simulator", "--data-dir", str(tmp_path)]) == 0
    assert captured["data_dir"] == tmp_path
    assert captured["options"] == {"initial_ip": "本地模拟器", "simulator_mode": True}
    controller = captured["factory"]("ignored")  # type: ignore[operator]
    assert isinstance(controller._client, SimulatedTurntableClient)


def test_package_smoke_option_is_explicitly_simulator_only() -> None:
    from turntable_control.main import _argument_parser

    options = _argument_parser().parse_args(["--simulator", "--package-smoke"])
    assert options.simulator is True
    assert options.package_smoke is True


@pytest.mark.parametrize("direction", [Direction.CW, Direction.CCW])
def test_manual_motion_is_one_turn_and_global_limit_rejects_more_travel(
    tmp_path: Path, direction: Direction
) -> None:
    system = SimulatedSystem(tmp_path / direction.name)
    system.commission()
    system.controller.start(Mode.MANUAL, direction, 10.0)
    system.process()
    path = system.run_until_saved()

    status = system.controller.snapshot.status
    assert status is not None
    assert status.actual_position_deg == pytest.approx(360.0 * direction.value)
    assert len(read_rows(path)) == 360
    with pytest.raises(CommandRejected, match="方向空间不足"):
        system.controller.start(Mode.AUTO, direction, 1.0)
    with pytest.raises(CommandRejected, match="方向空间不足"):
        system.controller.start(Mode.MANUAL, direction, 1.0)


@pytest.mark.parametrize(
    ("mode", "expected_status"),
    [
        (Mode.MANUAL, RunStatus.MANUAL_STOPPED),
        (Mode.AUTO, RunStatus.AUTOMATIC_ABORTED),
    ],
)
def test_user_software_stop_exports_partial_exact_prefix(
    tmp_path: Path, mode: Mode, expected_status: RunStatus
) -> None:
    system = SimulatedSystem(tmp_path / mode.name)
    system.commission()
    system.controller.start(mode, Direction.CCW, 5.0)
    system.process()
    for _ in range(20):
        system.process(advance_ms=100)
    before = system.controller.snapshot.status
    assert before is not None and before.actual_velocity_deg_s < 0

    system.controller.stop()
    system.process()
    stopping = system.client.read_status()
    assert stopping.run_state is RunState.STOPPING
    assert stopping.run_status is expected_status
    path = system.run_until_saved()

    rows = read_rows(path)
    assert 1 <= len(rows) < 360
    assert [int(row["sequence"]) for row in rows] == list(range(1, len(rows) + 1))
    assert {row["run_status"] for row in rows} == {expected_status.name}


def test_stop_before_first_degree_saves_metadata_without_fabricating_event(tmp_path: Path) -> None:
    system = SimulatedSystem(tmp_path)
    system.commission()
    system.controller.start(Mode.AUTO, Direction.CW, 1.0)
    system.process()
    system.controller.stop()
    system.process()

    path = system.run_until_saved()
    rows = read_rows(path)
    assert len(rows) == 1
    assert rows[0]["run_status"] == "AUTOMATIC_ABORTED"
    assert rows[0]["sequence"] == ""
    assert rows[0]["travel_angle_deg"] == ""
    assert rows[0]["epoch_ms"] == ""
    assert system.client.read_status().event_count == 0
    system.controller.start(Mode.AUTO, Direction.CW, 1.0)


def test_heartbeat_loss_aborts_then_reconnect_does_not_resume_or_replay(tmp_path: Path) -> None:
    system = SimulatedSystem(tmp_path)
    system.commission()
    system.controller.start(Mode.AUTO, Direction.CW, 5.0)
    system.process()
    start_seq = system.client.start_command_seq

    system.clock.advance(1_100)
    overdue = system.client.read_status()
    assert overdue.run_state is RunState.STOPPING
    assert overdue.run_status is RunStatus.COMMUNICATION_ABORTED
    path = system.run_until_saved()
    stopped_position = float(read_rows(path)[-1]["actual_position_deg"])

    system.controller.disconnect()
    system.process()
    system.controller.connect()
    system.process()
    reconnected = system.client.read_status()
    assert system.client.start_command_seq == start_seq
    assert reconnected.run_status is RunStatus.COMMUNICATION_ABORTED
    assert reconnected.actual_position_deg == pytest.approx(stopped_position, abs=1.0)


def test_run_crossing_raw_u32_tick_wrap_exports_monotonic_timestamps(tmp_path: Path) -> None:
    system = SimulatedSystem(tmp_path, initial_plc_tick_ms=0xFFFF_FF00)
    system.commission()
    system.controller.start(Mode.AUTO, Direction.CW, 10.0)
    system.process()
    path = system.run_until_saved()

    rows = read_rows(path)
    raw_ticks = [int(row["plc_tick_ms"]) for row in rows]
    epochs = [int(row["epoch_ms"]) for row in rows]
    assert any(tick < 0x10000 for tick in raw_ticks)
    assert epochs == sorted(epochs)
    assert epochs[-1] - epochs[0] > 30_000


class FailFirstCsvStore:
    def __init__(self, root: Path) -> None:
        self._real = CsvStore(root)
        self.calls = 0

    def save_run(self, run, synchronizer):
        self.calls += 1
        if self.calls == 1:
            raise CsvSaveError("injected disk failure")
        return self._real.save_run(run, synchronizer)


def test_csv_failure_retains_buffer_and_explicit_retry_saves_then_acks(tmp_path: Path) -> None:
    system = SimulatedSystem(tmp_path)
    flaky = FailFirstCsvStore(tmp_path)
    system.controller = TurntableController(
        system.client,
        flaky,
        ClockSynchronizer(),
        epoch_ms=lambda: system.clock.epoch,
        monotonic_ms=lambda: system.clock.monotonic,
    )
    system.commission()
    system.controller.start(Mode.AUTO, Direction.CW, 10.0)
    system.process()

    for _ in range(1_000):
        system.process(advance_ms=100)
        if system.controller.snapshot.download_pending:
            break
    failed = system.controller.snapshot
    assert failed.download_pending
    assert failed.saved_csv is None
    assert system.client.read_status().status_flags & STATUS_BUFFER_READY
    assert system.client.read_status().event_count == 360

    system.controller.retry_download()
    system.process()
    path = system.run_until_saved()
    assert flaky.calls == 2
    assert len(read_rows(path)) == 360
    assert not system.client.read_status().status_flags & STATUS_BUFFER_READY


class GatedClockSynchronizer(ClockSynchronizer):
    def __init__(self) -> None:
        super().__init__()
        self.enabled = False

    def add_sample(self, pc_send_ms: int, plc_ms: int, pc_recv_ms: int):
        if not self.enabled:
            raise ValueError("injected unsynchronized clock")
        return super().add_sample(pc_send_ms, plc_ms, pc_recv_ms)


def test_no_clock_sample_keeps_buffer_until_reconnect_sync_and_retry(tmp_path: Path) -> None:
    system = SimulatedSystem(tmp_path)
    synchronizer = GatedClockSynchronizer()
    system.controller = TurntableController(
        system.client,
        CsvStore(tmp_path),
        synchronizer,
        epoch_ms=lambda: system.clock.epoch,
        monotonic_ms=lambda: system.clock.monotonic,
    )
    system.commission()
    system.controller.start(Mode.AUTO, Direction.CCW, 10.0)
    system.process()
    for _ in range(1_000):
        system.process(advance_ms=100)
        if system.controller.snapshot.download_pending:
            break

    assert synchronizer.best_sample is None
    assert system.controller.snapshot.download_pending
    assert system.client.read_status().event_count == 360
    assert not list(tmp_path.glob("*.csv"))

    synchronizer.enabled = True
    system.controller.disconnect()
    system.process()
    system.controller.connect()
    system.process()
    system.process(advance_ms=100)
    assert synchronizer.best_sample is not None
    system.controller.retry_download()
    system.process()
    path = system.run_until_saved()
    assert len(read_rows(path)) == 360
    assert system.client.start_command_seq == 1
