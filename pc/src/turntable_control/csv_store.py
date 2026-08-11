"""Durable, local CSV export for completed turntable runs.

This module only serializes already-read records.  It deliberately has no
communication, acknowledgement, PLC, or hardware dependency.
"""

from __future__ import annotations

import csv
import math
import os
import re
import warnings
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

from .domain import Direction, Mode, RunStatus
from .time_sync import ClockSynchronizer


_U32_MAX = 0xFFFF_FFFF
_SAFE_TEST_ID = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
_CHINA_TZ = timezone(timedelta(hours=8))
_FIELDNAMES = (
    "test_id",
    "mode",
    "direction",
    "speed_deg_s",
    "total_ratio",
    "acceleration_deg_s2",
    "deceleration_deg_s2",
    "stop_deceleration_deg_s2",
    "run_status",
    "run_start_plc_ms",
    "saved_at_epoch_ms",
    "best_rtt_ms",
    "sequence",
    "travel_angle_deg",
    "actual_position_deg",
    "elapsed_ms",
    "plc_tick_ms",
    "epoch_ms",
    "utc_timestamp",
    "china_timestamp",
)


class CsvSaveError(RuntimeError):
    """Raised when a run cannot be safely validated or persisted."""


@dataclass(frozen=True)
class RunMetadata:
    test_id: str
    mode: Mode
    direction: Direction
    speed_deg_s: float
    total_ratio: float
    acceleration_deg_s2: float
    deceleration_deg_s2: float
    stop_deceleration_deg_s2: float
    run_status: RunStatus
    run_start_plc_ms: int
    saved_at_epoch_ms: int


@dataclass(frozen=True)
class RunExport:
    metadata: RunMetadata
    events: Sequence[object]


class CsvStore:
    """Persist one validated run as a BOM-prefixed CSV without overwriting evidence.

    A surviving ``<test_id>.csv.lock`` means a process may have crashed while
    publishing.  Operators must first verify that no writer is active and
    inspect any retained ``.tmp`` and final evidence before manually removing
    that lock; automatic stale-lock deletion would risk overwriting evidence.
    """

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def save_run(self, run: RunExport, synchronizer: ClockSynchronizer) -> Path:
        """Write and atomically publish a CSV, retaining a failed temporary file."""
        try:
            metadata, rows = _validated_rows(run, synchronizer)
            root = self._root.resolve()
            final_path = (root / f"{metadata.test_id}.csv").resolve()
            temp_path = (root / f"{metadata.test_id}.csv.tmp").resolve()
            lock_path = (root / f"{metadata.test_id}.csv.lock").resolve()
            if final_path.parent != root or temp_path.parent != root or lock_path.parent != root:
                raise CsvSaveError("export path escapes the configured root")
        except CsvSaveError:
            raise
        except Exception as error:
            raise CsvSaveError(f"invalid run export: {error}") from error

        lock_fd: int | None = None
        published = False
        failure: CsvSaveError | None = None
        try:
            root.mkdir(parents=True, exist_ok=True)
            try:
                lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError as error:
                raise CsvSaveError(
                    f"CSV lock exists for {metadata.test_id!r}; inspect retained evidence and remove a stale lock manually"
                ) from error
            if final_path.exists():
                raise CsvSaveError(f"refusing to overwrite existing CSV: {final_path.name}")
            with temp_path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=_FIELDNAMES, dialect="excel")
                writer.writeheader()
                writer.writerows(rows)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, final_path)
            published = True
        except CsvSaveError as error:
            failure = error
        except Exception as error:
            failure = CsvSaveError(f"failed to save {metadata.test_id!r}: {error}")
        finally:
            if lock_fd is not None:
                cleanup_error = _release_lock(lock_fd, lock_path)
                if cleanup_error is not None:
                    if published:
                        warnings.warn(
                            "final CSV was safely published, but its lock needs manual inspection/removal: "
                            f"{cleanup_error}",
                            RuntimeWarning,
                            stacklevel=2,
                        )
                    else:
                        warnings.warn(
                            "CSV lock cleanup failed; original CSV save failure is retained and the lock needs "
                            f"manual inspection/removal: {cleanup_error}",
                            RuntimeWarning,
                            stacklevel=2,
                        )
        if failure is not None:
            raise failure
        if published:
            return final_path
        raise CsvSaveError("CSV save did not publish a final file")


def _validated_rows(run: RunExport, synchronizer: ClockSynchronizer) -> tuple[RunMetadata, list[dict[str, str]]]:
    if not isinstance(run, RunExport) or not isinstance(run.metadata, RunMetadata):
        raise CsvSaveError("run must contain RunMetadata")
    metadata = run.metadata
    if type(metadata.test_id) is not str or not _SAFE_TEST_ID.fullmatch(metadata.test_id):
        raise CsvSaveError("test_id must be 1..80 ASCII letters, digits, underscores, or hyphens")
    if type(metadata.mode) is not Mode:
        raise CsvSaveError("mode must be a Mode enum member")
    if type(metadata.direction) is not Direction:
        raise CsvSaveError("direction must be a Direction enum member")
    if type(metadata.run_status) is not RunStatus:
        raise CsvSaveError("run_status must be a RunStatus enum member")
    _validate_u32(metadata.run_start_plc_ms, "run_start_plc_ms")
    if type(metadata.saved_at_epoch_ms) is not int:
        raise CsvSaveError("saved_at_epoch_ms must be an integer")
    events = tuple(run.events)
    if not 1 <= len(events) <= 360:
        raise CsvSaveError("event count must be in 1..360")
    if metadata.mode is Mode.AUTO and metadata.run_status is RunStatus.COMPLETED and len(events) != 360:
        raise CsvSaveError("AUTO COMPLETED exports must contain exactly 360 events")
    best = synchronizer.best_sample
    if best is None:
        raise CsvSaveError("a clock sample is required before export")

    previous_elapsed = -1
    previous_epoch: int | None = None
    rows: list[dict[str, str]] = []
    for expected_sequence, event in enumerate(events, start=1):
        sequence = getattr(event, "sequence", None)
        travel_angle = getattr(event, "travel_angle_deg", None)
        position = getattr(event, "actual_position_deg", None)
        elapsed = getattr(event, "elapsed_ms", None)
        if type(sequence) is not int or sequence != expected_sequence:
            raise CsvSaveError("event sequences must be exactly 1..N")
        if type(travel_angle) is not int or travel_angle != expected_sequence:
            raise CsvSaveError("event travel angles must equal sequences exactly within 1..360")
        if type(elapsed) is not int or not 0 <= elapsed <= _U32_MAX or elapsed < previous_elapsed:
            raise CsvSaveError("event elapsed values must be nondecreasing raw u32 values")
        if type(position) not in (int, float) or isinstance(position, bool) or not math.isfinite(position):
            raise CsvSaveError("event positions must be finite numbers")
        plc_tick = (metadata.run_start_plc_ms + elapsed) & _U32_MAX
        try:
            epoch_ms = synchronizer.event_to_epoch_ms(metadata.run_start_plc_ms, elapsed)
        except Exception as error:
            raise CsvSaveError(f"event {sequence} timestamp is invalid: {error}") from error
        if previous_epoch is not None and epoch_ms < previous_epoch:
            raise CsvSaveError("event timestamps must be nondecreasing")
        rows.append(
            {
                "test_id": metadata.test_id,
                "mode": metadata.mode.name,
                "direction": metadata.direction.name,
                "speed_deg_s": _format_decimal(metadata.speed_deg_s),
                "total_ratio": _format_decimal(metadata.total_ratio),
                "acceleration_deg_s2": _format_decimal(metadata.acceleration_deg_s2),
                "deceleration_deg_s2": _format_decimal(metadata.deceleration_deg_s2),
                "stop_deceleration_deg_s2": _format_decimal(metadata.stop_deceleration_deg_s2),
                "run_status": metadata.run_status.name,
                "run_start_plc_ms": str(metadata.run_start_plc_ms),
                "saved_at_epoch_ms": str(metadata.saved_at_epoch_ms),
                "best_rtt_ms": str(best.round_trip_ms),
                "sequence": str(sequence),
                "travel_angle_deg": str(travel_angle),
                "actual_position_deg": _format_decimal(position),
                "elapsed_ms": str(elapsed),
                "plc_tick_ms": str(plc_tick),
                "epoch_ms": str(epoch_ms),
                "utc_timestamp": _timestamp(epoch_ms, timezone.utc),
                "china_timestamp": _timestamp(epoch_ms, _CHINA_TZ),
            }
        )
        previous_elapsed = elapsed
        previous_epoch = epoch_ms
    return metadata, rows


def _validate_u32(value: object, field: str) -> None:
    if type(value) is not int or not 0 <= value <= _U32_MAX:
        raise CsvSaveError(f"{field} must be a raw unsigned 32-bit integer")


def _release_lock(lock_fd: int, lock_path: Path) -> str | None:
    """Release one owned lock, retaining it whenever cleanup cannot be completed."""
    try:
        os.close(lock_fd)
    except OSError as error:
        return f"could not close lock ({error})"
    try:
        os.unlink(lock_path)
    except FileNotFoundError:
        return None
    except OSError as error:
        return f"could not remove lock ({error})"
    return None


def _format_decimal(value: object) -> str:
    if type(value) not in (int, float) or isinstance(value, bool) or not math.isfinite(value):
        raise CsvSaveError("numeric metadata must be finite")
    return format(value, ".15g")


def _timestamp(epoch_ms: int, tz: timezone) -> str:
    instant = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=epoch_ms)
    return instant.astimezone(tz).isoformat(timespec="milliseconds").replace("+00:00", "Z")
