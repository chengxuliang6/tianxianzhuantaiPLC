from __future__ import annotations

import csv
import math
import os
from pathlib import Path
from threading import Barrier, Event, Thread
from unittest.mock import Mock

import pytest

from turntable_control.csv_store import CsvSaveError, CsvStore, RunExport, RunMetadata
from turntable_control.domain import Direction, Mode, RunStatus
from turntable_control.modbus_client import EventRecord
from turntable_control.time_sync import ClockSynchronizer


def sample_run(
    *,
    test_id: str = "test-001",
    count: int = 2,
    run_status: RunStatus = RunStatus.AUTOMATIC_ABORTED,
) -> RunExport:
    events = tuple(
        EventRecord(index, index, index + 0.25, index * 100)
        for index in range(1, count + 1)
    )
    return RunExport(
        RunMetadata(
            test_id=test_id,
            mode=Mode.AUTO,
            direction=Direction.CW,
            speed_deg_s=2.5,
            total_ratio=50.0,
            acceleration_deg_s2=5.0,
            deceleration_deg_s2=5.0,
            stop_deceleration_deg_s2=10.0,
            run_status=run_status,
            run_start_plc_ms=0xFFFF_FF00,
            saved_at_epoch_ms=1_700_000_000_123,
        ),
        events,
    )


def synced_clock() -> ClockSynchronizer:
    clock = ClockSynchronizer()
    clock.add_sample(pc_send_ms=1_700_000_000_000, plc_ms=0xFFFF_FF00, pc_recv_ms=1_700_000_000_000)
    return clock


def test_save_run_writes_bom_header_360_rows_and_time_fields(tmp_path: Path) -> None:
    output = CsvStore(tmp_path).save_run(
        sample_run(count=360, run_status=RunStatus.COMPLETED), synced_clock()
    )

    assert output == tmp_path / "test-001.csv"
    assert output.read_bytes().startswith(b"\xef\xbb\xbf")
    with output.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 360
    assert rows[0]["test_id"] == "test-001"
    assert rows[0]["mode"] == "AUTO"
    assert rows[0]["direction"] == "CW"
    assert rows[0]["run_status"] == "COMPLETED"
    assert rows[0]["plc_tick_ms"] == str(0xFFFF_FF00 + 100)
    assert rows[0]["utc_timestamp"] == "2023-11-14T22:13:20.100Z"
    assert rows[0]["china_timestamp"] == "2023-11-15T06:13:20.100+08:00"
    assert rows[0]["best_rtt_ms"] == "0"


@pytest.mark.parametrize("test_id", ["", "a/b", "a\\b", "..", "=SUM(A1)", "bad space", "a" * 81])
def test_save_run_rejects_unsafe_test_id_before_opening_a_file(tmp_path: Path, test_id: str) -> None:
    with pytest.raises(CsvSaveError):
        CsvStore(tmp_path).save_run(sample_run(test_id=test_id), synced_clock())
    assert not list(tmp_path.iterdir())


def test_save_run_rejects_malformed_records_before_opening_file(tmp_path: Path) -> None:
    run = sample_run()
    malformed = RunExport(run.metadata, (EventRecord(1, 2, math.inf, 0), EventRecord(2, 1, 0, 1)))

    with pytest.raises(CsvSaveError):
        CsvStore(tmp_path).save_run(malformed, synced_clock())
    assert not list(tmp_path.iterdir())


def test_save_run_requires_each_travel_angle_to_equal_its_sequence(tmp_path: Path) -> None:
    run = sample_run()
    gap = RunExport(run.metadata, (EventRecord(1, 1, 1.0, 0), EventRecord(2, 3, 3.0, 1)))

    with pytest.raises(CsvSaveError, match="travel angles must equal sequences"):
        CsvStore(tmp_path).save_run(gap, synced_clock())
    assert not list(tmp_path.iterdir())


def test_save_run_requires_auto_completed_to_have_all_360_events(tmp_path: Path) -> None:
    incomplete = sample_run(count=359, run_status=RunStatus.COMPLETED)

    with pytest.raises(CsvSaveError, match="AUTO COMPLETED.*360"):
        CsvStore(tmp_path).save_run(incomplete, synced_clock())
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("field", ["mode", "direction", "run_status"])
@pytest.mark.parametrize("invalid", ["plain-text", "=formula", "+formula", "-formula", "@formula"])
def test_save_run_rejects_non_enum_metadata_before_opening_file(
    tmp_path: Path, field: str, invalid: str
) -> None:
    run = sample_run()
    values = {name: getattr(run.metadata, name) for name in ("mode", "direction", "run_status")}
    values[field] = invalid
    metadata = RunMetadata(
        test_id=run.metadata.test_id,
        speed_deg_s=run.metadata.speed_deg_s,
        total_ratio=run.metadata.total_ratio,
        acceleration_deg_s2=run.metadata.acceleration_deg_s2,
        deceleration_deg_s2=run.metadata.deceleration_deg_s2,
        stop_deceleration_deg_s2=run.metadata.stop_deceleration_deg_s2,
        run_start_plc_ms=run.metadata.run_start_plc_ms,
        saved_at_epoch_ms=run.metadata.saved_at_epoch_ms,
        **values,
    )

    with pytest.raises(CsvSaveError):
        CsvStore(tmp_path).save_run(RunExport(metadata, run.events), synced_clock())
    assert not list(tmp_path.iterdir())


def test_save_run_refuses_existing_final_and_keeps_it(tmp_path: Path) -> None:
    final = tmp_path / "test-001.csv"
    final.write_text("evidence", encoding="utf-8")

    with pytest.raises(CsvSaveError):
        CsvStore(tmp_path).save_run(sample_run(), synced_clock())
    assert final.read_text(encoding="utf-8") == "evidence"


def test_save_run_refuses_existing_final_when_a_stale_lock_also_exists(tmp_path: Path) -> None:
    final = tmp_path / "test-001.csv"
    lock = tmp_path / "test-001.csv.lock"
    final.write_text("evidence", encoding="utf-8")
    lock.write_text("manual recovery required", encoding="utf-8")

    with pytest.raises(CsvSaveError, match="lock"):
        CsvStore(tmp_path).save_run(sample_run(), synced_clock())
    assert final.read_text(encoding="utf-8") == "evidence"
    assert lock.exists()


def test_save_run_cannot_escape_resolved_root(tmp_path: Path) -> None:
    root = tmp_path / "exports"
    result = CsvStore(root).save_run(sample_run(test_id="safe_name"), synced_clock())

    assert result.parent.resolve() == root.resolve()
    assert result.exists()


def test_save_run_uses_replace_after_flush_and_fsync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    replace = Mock(wraps=os.replace)
    fsync = Mock(wraps=os.fsync)
    monkeypatch.setattr(os, "replace", replace)
    monkeypatch.setattr(os, "fsync", fsync)

    output = CsvStore(tmp_path).save_run(sample_run(), synced_clock())

    assert output.exists()
    assert fsync.call_count == 1
    replace.assert_called_once_with(tmp_path / "test-001.csv.tmp", tmp_path / "test-001.csv")
    assert not (tmp_path / "test-001.csv.lock").exists()


def test_concurrent_save_run_has_exactly_one_publisher(tmp_path: Path) -> None:
    store = CsvStore(tmp_path)
    barrier = Barrier(3)
    outcomes: list[str] = []

    def save() -> None:
        barrier.wait()
        try:
            store.save_run(sample_run(), synced_clock())
            outcomes.append("saved")
        except CsvSaveError:
            outcomes.append("rejected")

    first = Thread(target=save)
    second = Thread(target=save)
    first.start()
    second.start()
    barrier.wait()
    first.join(timeout=2)
    second.join(timeout=2)

    assert sorted(outcomes) == ["rejected", "saved"]
    assert (tmp_path / "test-001.csv").exists()
    assert not (tmp_path / "test-001.csv.lock").exists()


def test_second_thread_cannot_publish_while_first_holds_lock_through_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_replace = os.replace
    entered_replace = Event()
    release_replace = Event()
    failures: list[BaseException] = []

    def blocked_replace(source: Path, destination: Path) -> None:
        entered_replace.set()
        assert release_replace.wait(timeout=2)
        original_replace(source, destination)

    monkeypatch.setattr(os, "replace", blocked_replace)

    def save_first() -> None:
        try:
            CsvStore(tmp_path).save_run(sample_run(), synced_clock())
        except BaseException as error:
            failures.append(error)

    first = Thread(target=save_first)
    first.start()
    assert entered_replace.wait(timeout=2)
    with pytest.raises(CsvSaveError, match="lock"):
        CsvStore(tmp_path).save_run(sample_run(), synced_clock())
    release_replace.set()
    first.join(timeout=2)

    assert not failures
    assert (tmp_path / "test-001.csv").exists()
    assert not (tmp_path / "test-001.csv.lock").exists()


@pytest.mark.parametrize("cleanup", ["close", "unlink"])
def test_lock_cleanup_failure_after_publish_warns_but_returns_final(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cleanup: str
) -> None:
    lock = tmp_path / "test-001.csv.lock"
    if cleanup == "close":
        original_close = os.close

        def failing_close(descriptor: int) -> None:
            original_close(descriptor)
            raise OSError("close failed")

        monkeypatch.setattr(os, "close", failing_close)
    else:
        original_unlink = os.unlink

        def failing_unlink(path: str | Path) -> None:
            if Path(path) == lock:
                raise OSError("unlink failed")
            original_unlink(path)

        monkeypatch.setattr(os, "unlink", failing_unlink)

    with pytest.warns(RuntimeWarning, match="final CSV was safely published"):
        output = CsvStore(tmp_path).save_run(sample_run(), synced_clock())

    assert output == tmp_path / "test-001.csv"
    assert output.exists()
    assert not (tmp_path / "test-001.csv.tmp").exists()
    assert lock.exists()


@pytest.mark.parametrize("cleanup", ["close", "unlink"])
def test_lock_cleanup_failure_before_publish_warns_and_retains_csv_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cleanup: str
) -> None:
    lock = tmp_path / "test-001.csv.lock"
    monkeypatch.setattr(os, "fsync", Mock(side_effect=OSError("disk full")))
    if cleanup == "close":
        original_close = os.close

        def failing_close(descriptor: int) -> None:
            original_close(descriptor)
            raise OSError("close failed")

        monkeypatch.setattr(os, "close", failing_close)
    else:
        original_unlink = os.unlink

        def failing_unlink(path: str | Path) -> None:
            if Path(path) == lock:
                raise OSError("unlink failed")
            original_unlink(path)

        monkeypatch.setattr(os, "unlink", failing_unlink)

    with pytest.warns(RuntimeWarning, match="original CSV save failure is retained"):
        with pytest.raises(CsvSaveError, match="disk full"):
            CsvStore(tmp_path).save_run(sample_run(), synced_clock())

    assert not (tmp_path / "test-001.csv").exists()
    assert (tmp_path / "test-001.csv.tmp").exists()
    assert lock.exists()


@pytest.mark.parametrize("operation", ["fsync", "replace"])
def test_save_run_failure_preserves_temporary_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str) -> None:
    monkeypatch.setattr(os, operation, Mock(side_effect=OSError("disk full")))

    with pytest.raises(CsvSaveError):
        CsvStore(tmp_path).save_run(sample_run(), synced_clock())

    assert (tmp_path / "test-001.csv.tmp").exists()
    assert not (tmp_path / "test-001.csv").exists()


def test_save_run_write_failure_preserves_created_temporary_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original_writer = csv.DictWriter.writerow
    monkeypatch.setattr(csv.DictWriter, "writerow", Mock(side_effect=OSError("write failed")))

    with pytest.raises(CsvSaveError):
        CsvStore(tmp_path).save_run(sample_run(), synced_clock())

    assert (tmp_path / "test-001.csv.tmp").exists()
    monkeypatch.setattr(csv.DictWriter, "writerow", original_writer)


def test_csv_module_does_not_import_or_call_plc_client_or_acknowledgement() -> None:
    source = Path(__file__).parents[1] / "src" / "turntable_control" / "csv_store.py"
    assert "modbus_client" not in source.read_text(encoding="utf-8")
    assert "acknowledge_buffer" not in source.read_text(encoding="utf-8")
