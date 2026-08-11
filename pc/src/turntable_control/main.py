"""Import-safe desktop entry point for the turntable operator interface."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from collections.abc import Sequence

from PySide6.QtWidgets import QApplication

from .controller import TurntableController
from .csv_store import CsvStore
from .modbus_client import TurntableModbusClient
from .time_sync import ClockSynchronizer
from .ui import MainWindow


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="天线测试转台控制")
    parser.add_argument("--plc-ip", default="", help="仅预填 PLC IPv4 地址，不会自动连接")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path.cwd() / "data",
        help="CSV 数据保存目录",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Create the GUI; all PLC objects remain deferred until Connect is clicked."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    options = _argument_parser().parse_args(arguments)
    data_dir = Path(options.data_dir)

    app = QApplication.instance()
    if app is None:
        app = QApplication([sys.argv[0], *arguments])
    app.setApplicationName("天线测试转台控制")

    def controller_factory(plc_ip: str) -> TurntableController:
        client = TurntableModbusClient(host=plc_ip)
        return TurntableController(
            client,
            CsvStore(data_dir),
            ClockSynchronizer(),
            epoch_ms=lambda: time.time_ns() // 1_000_000,
            monotonic_ms=lambda: time.monotonic_ns() // 1_000_000,
        )

    window = MainWindow(controller_factory, data_dir, initial_ip=options.plc_ip)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
