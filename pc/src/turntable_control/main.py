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
        "--simulator",
        action="store_true",
        help="使用本地模拟器（无 PLC、无网络）",
    )
    parser.add_argument("--package-smoke", action="store_true", help=argparse.SUPPRESS)
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
    if options.package_smoke and not options.simulator:
        raise SystemExit("--package-smoke 只能与 --simulator 一起使用")
    data_dir = Path(options.data_dir)

    app = QApplication.instance()
    if app is None:
        app = QApplication([sys.argv[0], *arguments])
    app.setApplicationName("天线测试转台控制")

    def controller_factory(plc_ip: str) -> TurntableController:
        if options.simulator:
            from .simulated_client import SimulatedTurntableClient

            client = SimulatedTurntableClient(
                monotonic_ms=lambda: time.monotonic_ns() // 1_000_000
            )
        else:
            client = TurntableModbusClient(host=plc_ip)
        return TurntableController(
            client,
            CsvStore(data_dir),
            ClockSynchronizer(),
            epoch_ms=lambda: time.time_ns() // 1_000_000,
            monotonic_ms=lambda: time.monotonic_ns() // 1_000_000,
        )

    window_options: dict[str, object] = {
        "initial_ip": "本地模拟器" if options.simulator else options.plc_ip,
    }
    if options.simulator:
        window_options["simulator_mode"] = True
    window = MainWindow(controller_factory, data_dir, **window_options)
    window.show()
    if options.package_smoke:
        from PySide6.QtCore import QTimer

        QTimer.singleShot(250, app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
