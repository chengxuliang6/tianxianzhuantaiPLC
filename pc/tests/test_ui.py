from __future__ import annotations

from pathlib import Path
from dataclasses import replace
from threading import Thread, get_ident

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QDesktopServices, QKeySequence
from PySide6.QtWidgets import QApplication, QAbstractButton, QGroupBox, QLabel, QMessageBox

from turntable_control.controller import ControllerSnapshot, ControllerStopped
from turntable_control.domain import Direction, Mode, RunState, RunStatus
from turntable_control.modbus_client import StatusSnapshot
from turntable_control.ui import ControllerBridge, MainWindow


def unused_factory(plc_ip: str) -> object:
    raise AssertionError(f"controller factory must not run during construction: {plc_ip}")


def status_snapshot(**changes: object) -> StatusSnapshot:
    status = StatusSnapshot(
        run_state=RunState.READY,
        status_flags=0x0003,
        fault_code=0,
        actual_position_deg=12.3456,
        target_position_deg=-2.0,
        actual_velocity_deg_s=-1.2346,
        heartbeat_echo=17,
        start_ack_seq=0,
        stop_ack_seq=0,
        set_zero_ack_seq=0,
        reset_fault_ack_seq=0,
        power_ack_seq=0,
        buffer_acked_seq=0,
        event_count=0,
        event_generation=0,
        run_status=RunStatus.IDLE,
        run_start_plc_ms=0,
        protocol_version=1,
        word_order_probe=0x12345678,
        time_sync_request_seq=0,
        plc_tick_ms=100,
        time_sync_response_seq=0,
    )
    return replace(status, **changes)


def connected_snapshot(**changes: object) -> ControllerSnapshot:
    snapshot = ControllerSnapshot(connected=True, status=status_snapshot())
    return replace(snapshot, **changes)


class FakeController:
    def __init__(self) -> None:
        self.snapshot = ControllerSnapshot()
        self.calls: list[tuple[object, ...]] = []
        self.snapshot_callbacks: list[object] = []
        self.error_callbacks: list[object] = []
        self.saved_callbacks: list[object] = []
        self.failures: dict[str, Exception] = {}
        self.unsubscribe_count = 0

    def _record(self, name: str, *args: object) -> None:
        self.calls.append((name, *args))
        error = self.failures.pop(name, None)
        if error is not None:
            raise error

    def _subscribe(self, name: str, callbacks: list[object], callback: object):
        self.calls.append((name,))
        callbacks.append(callback)

        def unsubscribe() -> None:
            if callback in callbacks:
                callbacks.remove(callback)
                self.unsubscribe_count += 1

        return unsubscribe

    def on_snapshot(self, callback: object):
        return self._subscribe("on_snapshot", self.snapshot_callbacks, callback)

    def on_error(self, callback: object):
        return self._subscribe("on_error", self.error_callbacks, callback)

    def on_run_saved(self, callback: object):
        return self._subscribe("on_run_saved", self.saved_callbacks, callback)

    def connect(self) -> None:
        self._record("connect")

    def disconnect(self) -> None:
        self._record("disconnect")

    def start_background(self) -> None:
        self._record("start_background")

    def start(self, mode: Mode, direction: Direction, speed: float) -> None:
        self._record("start", mode, direction, speed)

    def stop(self) -> None:
        self._record("stop")

    def set_zero(self) -> None:
        self._record("set_zero")

    def toggle_power(self) -> None:
        self._record("toggle_power")

    def reset_alarm(self) -> None:
        self._record("reset_alarm")

    def retry_download(self) -> None:
        self._record("retry_download")

    def shutdown(self, timeout: float | None = None) -> None:
        self._record("shutdown", timeout)

    def emit_snapshot(self, snapshot: ControllerSnapshot) -> None:
        self.snapshot = snapshot
        for callback in tuple(self.snapshot_callbacks):
            callback(snapshot)  # type: ignore[operator]

    def emit_error(self, message: str) -> None:
        for callback in tuple(self.error_callbacks):
            callback(message)  # type: ignore[operator]

    def emit_saved(self, path: Path) -> None:
        for callback in tuple(self.saved_callbacks):
            callback(path)  # type: ignore[operator]


class SubscriptionFailController(FakeController):
    def __init__(self, fail_at: int) -> None:
        super().__init__()
        self.fail_at = fail_at
        self.subscription_attempt = 0

    def _subscribe(self, name: str, callbacks: list[object], callback: object):
        self.subscription_attempt += 1
        if self.subscription_attempt == self.fail_at:
            raise RuntimeError(f"订阅位置 {self.fail_at} 失败")
        return super()._subscribe(name, callbacks, callback)


class InspectShutdownController(FakeController):
    def __init__(self) -> None:
        super().__init__()
        self.bridge_active_during_shutdown: bool | None = None

    def shutdown(self, timeout: float | None = None) -> None:
        self.bridge_active_during_shutdown = all(
            (self.snapshot_callbacks, self.error_callbacks, self.saved_callbacks)
        )
        super().shutdown(timeout)


class FakeFactory:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.controllers: list[FakeController] = []

    def __call__(self, plc_ip: str) -> FakeController:
        self.calls.append(plc_ip)
        controller = FakeController()
        self.controllers.append(controller)
        return controller


def make_connected_window(qtbot, data_dir: Path, ip: str = "192.0.2.20"):
    factory = FakeFactory()
    window = MainWindow(factory, data_dir, initial_ip=ip)
    qtbot.addWidget(window)
    window.show()
    qtbot.mouseClick(window.connect_button, Qt.LeftButton)
    controller = factory.controllers[0]
    controller.emit_snapshot(connected_snapshot())
    qtbot.waitUntil(lambda: window.connection_status_label.text() == "在线")
    return window, factory, controller


def make_window(qtbot, data_dir: Path, *, initial_ip: str = "") -> MainWindow:
    window = MainWindow(unused_factory, data_dir, initial_ip=initial_ip)
    qtbot.addWidget(window)
    window.show()
    return window


def test_five_operator_sections_and_required_chinese_labels_exist(qtbot, tmp_path: Path) -> None:
    window = make_window(qtbot, tmp_path)

    section_titles = {box.title() for box in window.findChildren(QGroupBox)}
    assert {
        "连接与设备状态",
        "角度状态",
        "运行设置",
        "操作与数据记录",
        "高级参数",
    }.issubset(section_titles)
    visible_text = " ".join(
        widget.text()
        for widget in (*window.findChildren(QLabel), *window.findChildren(QAbstractButton))
    )
    for text in (
        "PLC IPv4",
        "当前位置设零",
        "重试下载",
        "打开数据目录",
        "-360° ～ +360°",
        "由 PLC 轴状态综合判断 / 现场待验证",
    ):
        assert text in visible_text
    assert [window.direction_combo.itemText(index) for index in range(window.direction_combo.count())] == [
        "正转",
        "反转",
    ]
    assert not window.advanced_panel.isVisible()


def test_initial_ip_is_blank_unless_explicitly_prefilled(qtbot, tmp_path: Path) -> None:
    blank = make_window(qtbot, tmp_path)
    assert blank.ip_edit.text() == ""
    blank.close()

    prefilled = make_window(qtbot, tmp_path, initial_ip="192.0.2.10")
    assert prefilled.ip_edit.text() == "192.0.2.10"


def test_software_stop_is_exact_red_non_emergency_and_always_enabled(qtbot, tmp_path: Path) -> None:
    window = make_window(qtbot, tmp_path)

    assert window.stop_button.text() == "停止（软件）"
    assert "background-color" in window.stop_button.styleSheet()
    assert "red" in window.stop_button.styleSheet() or "#" in window.stop_button.styleSheet()
    assert "不能替代实体急停" in window.stop_button.toolTip()
    assert window.stop_button.isEnabled()

    assert window._software_stop_shortcut.key() == QKeySequence(Qt.Key_Space)
    window._software_stop_shortcut.activated.emit()
    assert window.stop_button.isEnabled()
    assert "PLC未连接" in window.message_label.text()


def test_advanced_phase_one_values_are_collapsed_read_only_and_truthful(qtbot, tmp_path: Path) -> None:
    window = make_window(qtbot, tmp_path)

    assert not window.advanced_panel.isVisible()
    qtbot.mouseClick(window.advanced_toggle, Qt.LeftButton)
    assert window.advanced_panel.isVisible()
    assert window.ratio_value.text() == "50:1"
    assert window.acceleration_value.text() == "5°/s²"
    assert window.deceleration_value.text() == "5°/s²"
    assert window.stop_deceleration_value.text() == "10°/s²"
    assert window.backlash_value.text() == "0°"
    for widget in (
        window.ratio_value,
        window.acceleration_value,
        window.deceleration_value,
        window.stop_deceleration_value,
        window.backlash_value,
    ):
        assert not widget.isEnabled()
        assert "第一阶段" in widget.toolTip()
        assert "不可编辑" in widget.toolTip()


def test_mode_direction_and_speed_values_map_exactly_to_domain(qtbot, tmp_path: Path) -> None:
    window = make_window(qtbot, tmp_path)

    assert [window.mode_combo.itemData(index) for index in range(window.mode_combo.count())] == [
        Mode.MANUAL,
        Mode.AUTO,
    ]
    assert [
        window.direction_combo.itemData(index) for index in range(window.direction_combo.count())
    ] == [Direction.CW, Direction.CCW]
    assert [window.speed_combo.itemData(index) for index in range(window.speed_combo.count())] == [
        1.0,
        2.0,
        4.0,
        5.0,
        10.0,
    ]


def test_snapshot_formats_values_and_ready_interlocks(qtbot, tmp_path: Path) -> None:
    window = make_window(qtbot, tmp_path)

    window.apply_snapshot(connected_snapshot())

    assert window.connection_status_label.text() == "在线"
    assert window.heartbeat_label.text() == "17"
    assert window.power_status_label.text() == "已上电 / 已就绪"
    assert window.fault_label.text() == "0"
    assert window.actual_position_label.text() == "+12.346°"
    assert window.target_position_label.text() == "-2.000°"
    assert window.actual_velocity_label.text() == "-1.235°/s"
    assert window.zero_status_label.text() == "有效"
    assert window.limit_label.text() == "范围内"
    assert window.run_status_label.text() == "空闲"
    assert window.start_button.isEnabled()


def test_running_and_stopping_lock_settings_but_never_stop(qtbot, tmp_path: Path) -> None:
    window = make_window(qtbot, tmp_path)
    locked_widgets = (
        window.ip_edit,
        window.connect_button,
        window.mode_combo,
        window.direction_combo,
        window.speed_combo,
        window.set_zero_button,
        window.power_button,
        window.reset_button,
        window.advanced_toggle,
    )

    for state in (RunState.MANUAL_RUNNING, RunState.AUTO_RUNNING, RunState.STOPPING):
        window.apply_snapshot(
            connected_snapshot(status=status_snapshot(run_state=state, run_status=RunStatus.RUNNING))
        )
        assert all(not widget.isEnabled() for widget in locked_widgets)
        assert not window.start_button.isEnabled()
        assert window.disconnect_button.isEnabled()
        assert window.stop_button.isEnabled()

    window.apply_snapshot(connected_snapshot())
    assert window.ip_edit.isEnabled()
    assert window.disconnect_button.isEnabled()
    assert window.mode_combo.isEnabled()
    assert window.direction_combo.isEnabled()
    assert window.speed_combo.isEnabled()
    assert window.set_zero_button.isEnabled()
    assert window.power_button.isEnabled()
    assert window.reset_button.isEnabled()
    assert window.advanced_toggle.isEnabled()
    assert window.stop_button.isEnabled()


def test_unknown_run_state_fails_closed_and_displays_raw_value(qtbot, tmp_path: Path) -> None:
    window = make_window(qtbot, tmp_path)

    window.apply_snapshot(connected_snapshot(status=status_snapshot(run_state=99)))

    assert window.run_state_label.text() == "未知（99）"
    assert not window.start_button.isEnabled()


@pytest.mark.parametrize(
    "status",
    [
        status_snapshot(run_state=99, run_status=RunStatus.IDLE),
        status_snapshot(run_state=RunState.READY, run_status=77),
    ],
    ids=("raw-run-state", "raw-run-status"),
)
def test_raw_state_or_status_allows_only_stop_and_disconnect(
    qtbot, tmp_path: Path, status: StatusSnapshot
) -> None:
    window = make_window(qtbot, tmp_path)

    window.apply_snapshot(connected_snapshot(status=status, download_pending=True))

    for widget in (
        window.ip_edit,
        window.connect_button,
        window.mode_combo,
        window.direction_combo,
        window.speed_combo,
        window.start_button,
        window.set_zero_button,
        window.power_button,
        window.reset_button,
        window.retry_button,
        window.advanced_toggle,
        window.open_data_button,
    ):
        assert not widget.isEnabled()
    assert window.stop_button.isEnabled()
    assert window.disconnect_button.isEnabled()


def test_start_requires_exact_ready_and_idle(qtbot, tmp_path: Path) -> None:
    window = make_window(qtbot, tmp_path)

    window.apply_snapshot(
        connected_snapshot(status=status_snapshot(run_state=RunState.READY, run_status=RunStatus.RUNNING))
    )

    assert not window.start_button.isEnabled()
    assert window.stop_button.isEnabled()

    window.apply_snapshot(connected_snapshot(status=status_snapshot(run_state=2, run_status=1)))
    assert window.run_state_label.text() == "未知（2）"
    assert window.run_status_label.text() == "未知（1）"
    assert not window.start_button.isEnabled()


def test_terminal_statuses_event_count_pending_retry_and_saved_path(qtbot, tmp_path: Path) -> None:
    window = make_window(qtbot, tmp_path)
    expected = {
        RunStatus.IDLE: "空闲",
        RunStatus.RUNNING: "运行中",
        RunStatus.COMPLETED: "已完成",
        RunStatus.MANUAL_STOPPED: "手动停止",
        RunStatus.AUTOMATIC_ABORTED: "自动中止",
        RunStatus.COMMUNICATION_ABORTED: "通信中止",
        RunStatus.FAULTED: "故障终止",
        77: "未知（77）",
    }
    for raw_status, text in expected.items():
        window.apply_snapshot(
            connected_snapshot(
                status=status_snapshot(run_status=raw_status, event_count=23),
                active_test_id="run_123",
            )
        )
        assert window.run_status_label.text() == text
        assert window.event_count_label.text() == "23"
        assert window.test_id_label.text() == "run_123"

    unannounced_path = tmp_path / "not-announced.csv"
    window.apply_snapshot(connected_snapshot(download_pending=True, saved_csv=unannounced_path))
    assert window.csv_path_label.text() == "—"
    assert window.csv_status_label.text() == "待下载或保存"
    assert window.retry_button.isVisible()
    assert window.retry_button.isEnabled()

    saved_path = tmp_path / "run_123.csv"
    window.apply_saved_path(saved_path)
    assert window.csv_path_label.text() == str(saved_path)
    assert window.csv_status_label.text() == "已保存，等待 PLC 确认"


def test_csv_status_distinguishes_unsaved_ack_pending_and_acknowledged(
    qtbot, tmp_path: Path
) -> None:
    window = make_window(qtbot, tmp_path)
    saved_path = tmp_path / "durable.csv"

    window.apply_snapshot(connected_snapshot(download_pending=True))
    assert window.csv_status_label.text() == "待下载或保存"

    window.apply_saved_path(saved_path)
    assert window.csv_status_label.text() == "已保存，等待 PLC 确认"

    window.apply_snapshot(connected_snapshot(download_pending=True, saved_csv=saved_path))
    assert window.csv_status_label.text() == "已保存，等待 PLC 确认"

    window.apply_snapshot(connected_snapshot(download_pending=False, saved_csv=saved_path))
    assert window.csv_status_label.text() == "已保存"


def test_connect_rejects_blank_invalid_and_non_ipv4_inline(qtbot, tmp_path: Path) -> None:
    factory = FakeFactory()
    window = MainWindow(factory, tmp_path)
    qtbot.addWidget(window)
    window.show()

    for invalid in ("", "not-a-host", "999.1.1.1", "::1"):
        window.ip_edit.setText(invalid)
        qtbot.mouseClick(window.connect_button, Qt.LeftButton)
        assert "有效的 PLC IPv4" in window.message_label.text()
    assert factory.calls == []


def test_first_connect_registers_callbacks_queues_connect_then_starts_background(
    qtbot, tmp_path: Path
) -> None:
    factory = FakeFactory()
    window = MainWindow(factory, tmp_path, initial_ip="192.0.2.21")
    qtbot.addWidget(window)
    window.show()

    qtbot.mouseClick(window.connect_button, Qt.LeftButton)

    controller = factory.controllers[0]
    assert factory.calls == ["192.0.2.21"]
    assert [call[0] for call in controller.calls[:5]] == [
        "on_snapshot",
        "on_error",
        "on_run_saved",
        "connect",
        "start_background",
    ]
    assert not any(call[0] == "start" for call in controller.calls)


def test_first_connect_background_start_failure_cleans_up_and_allows_retry(
    qtbot, tmp_path: Path
) -> None:
    failed = FakeController()
    failed.failures["start_background"] = RuntimeError("后台线程启动失败")
    replacement = FakeController()
    controllers = [failed, replacement]

    def factory(_plc_ip: str) -> FakeController:
        return controllers.pop(0)

    window = MainWindow(factory, tmp_path, initial_ip="192.0.2.25")
    qtbot.addWidget(window)
    window.show()

    qtbot.mouseClick(window.connect_button, Qt.LeftButton)
    assert "后台线程启动失败" in window.message_label.text()
    assert failed.unsubscribe_count == 3
    assert ("shutdown", 2.0) in failed.calls
    assert window._controller is None
    assert window.connect_button.isEnabled()

    qtbot.mouseClick(window.connect_button, Qt.LeftButton)
    assert [call[0] for call in replacement.calls[-2:]] == ["connect", "start_background"]


def test_first_connect_cleanup_failure_retains_live_controller_bridge(
    qtbot, tmp_path: Path
) -> None:
    controller = FakeController()
    controller.failures["start_background"] = RuntimeError("后台线程启动失败")
    controller.failures["shutdown"] = ControllerStopped("清理未停止")
    window = MainWindow(lambda _ip: controller, tmp_path, initial_ip="192.0.2.29")
    qtbot.addWidget(window)
    window.show()

    qtbot.mouseClick(window.connect_button, Qt.LeftButton)

    assert window._controller is controller
    assert window._bridge is not None
    assert all((controller.snapshot_callbacks, controller.error_callbacks, controller.saved_callbacks))
    assert "后台线程启动失败" in window.message_label.text()
    assert "清理未停止" in window.message_label.text()

    worker = Thread(target=controller.emit_error, args=("仍可接收控制器错误",))
    worker.start()
    worker.join()
    QApplication.processEvents()
    assert "仍可接收控制器错误" in window.message_label.text()


@pytest.mark.parametrize("fail_at", [1, 2, 3])
def test_partial_bridge_subscription_unwinds_and_bounded_shutdowns_local_controller(
    qtbot, tmp_path: Path, fail_at: int
) -> None:
    controller = SubscriptionFailController(fail_at)
    window = MainWindow(lambda _ip: controller, tmp_path, initial_ip="192.0.2.26")
    qtbot.addWidget(window)
    window.show()

    qtbot.mouseClick(window.connect_button, Qt.LeftButton)

    assert f"订阅位置 {fail_at} 失败" in window.message_label.text()
    assert controller.unsubscribe_count == fail_at - 1
    assert not controller.snapshot_callbacks
    assert not controller.error_callbacks
    assert not controller.saved_callbacks
    assert ("shutdown", 2.0) in controller.calls
    assert window._controller is None
    assert window._bridge is None


def test_reconnect_reuses_controller_and_changed_disconnected_ip_replaces_safely(
    qtbot, tmp_path: Path
) -> None:
    window, factory, first = make_connected_window(qtbot, tmp_path)
    first.emit_snapshot(ControllerSnapshot())
    qtbot.waitUntil(lambda: window.connect_button.isEnabled())

    qtbot.mouseClick(window.connect_button, Qt.LeftButton)
    assert factory.calls == ["192.0.2.20"]
    assert [call[0] for call in first.calls].count("connect") == 2
    assert [call[0] for call in first.calls].count("start_background") == 1
    assert not any(call[0] == "start" for call in first.calls)

    window.ip_edit.setText("192.0.2.22")
    qtbot.mouseClick(window.connect_button, Qt.LeftButton)
    assert factory.calls == ["192.0.2.20", "192.0.2.22"]
    assert first.unsubscribe_count == 3
    assert ("shutdown", 2.0) in first.calls
    assert [call[0] for call in factory.controllers[1].calls[-2:]] == [
        "connect",
        "start_background",
    ]


def test_replacement_shutdown_failure_restores_live_bridge_and_ignores_old_queue(
    qtbot, tmp_path: Path
) -> None:
    first = InspectShutdownController()
    replacement = FakeController()
    created: list[str] = []

    def factory(plc_ip: str) -> FakeController:
        created.append(plc_ip)
        return first if len(created) == 1 else replacement

    window = MainWindow(factory, tmp_path, initial_ip="192.0.2.27")
    qtbot.addWidget(window)
    window.show()
    qtbot.mouseClick(window.connect_button, Qt.LeftButton)
    first.emit_snapshot(ControllerSnapshot())
    old_bridge = window._bridge
    first.failures["shutdown"] = ControllerStopped("旧控制器未停止")

    queued = connected_snapshot(status=status_snapshot(actual_position_deg=88.0))
    worker = Thread(target=first.emit_snapshot, args=(queued,))
    worker.start()
    worker.join()
    window.ip_edit.setText("192.0.2.28")
    window._request_connect()

    assert first.bridge_active_during_shutdown is True
    assert window._controller is first
    assert window._bridge is old_bridge
    assert window._bridge is not None
    assert all((first.snapshot_callbacks, first.error_callbacks, first.saved_callbacks))
    assert created == ["192.0.2.27"]
    assert "旧控制器未停止" in window.message_label.text()

    fresh = connected_snapshot(status=status_snapshot(actual_position_deg=99.0))
    worker = Thread(target=first.emit_snapshot, args=(fresh,))
    worker.start()
    worker.join()
    QApplication.processEvents()
    assert window.actual_position_label.text() == "+99.000°"


def test_start_and_other_commands_use_controller_and_domain_values(qtbot, tmp_path: Path) -> None:
    window, _factory, controller = make_connected_window(qtbot, tmp_path)
    window.mode_combo.setCurrentIndex(1)
    window.direction_combo.setCurrentIndex(1)
    window.speed_combo.setCurrentIndex(4)

    qtbot.mouseClick(window.start_button, Qt.LeftButton)
    assert ("start", Mode.AUTO, Direction.CCW, 10.0) in controller.calls
    assert not window.mode_combo.isEnabled()
    assert window.stop_button.isEnabled()

    controller.emit_snapshot(
        connected_snapshot(
            status=status_snapshot(run_state=RunState.AUTO_RUNNING, run_status=RunStatus.RUNNING)
        )
    )
    controller.emit_snapshot(connected_snapshot())
    qtbot.mouseClick(window.power_button, Qt.LeftButton)
    qtbot.mouseClick(window.reset_button, Qt.LeftButton)
    controller.emit_snapshot(connected_snapshot(download_pending=True))
    qtbot.mouseClick(window.retry_button, Qt.LeftButton)
    assert ("toggle_power",) in controller.calls
    assert ("reset_alarm",) in controller.calls
    assert ("retry_download",) in controller.calls


def test_set_zero_requires_chinese_confirmation_and_never_opens_while_running(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    window, _factory, controller = make_connected_window(qtbot, tmp_path)
    questions: list[tuple[str, str]] = []
    answers = [QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes]

    def question(_parent, title: str, text: str, *_args, **_kwargs):
        questions.append((title, text))
        return answers.pop(0)

    monkeypatch.setattr(QMessageBox, "question", question)
    qtbot.mouseClick(window.set_zero_button, Qt.LeftButton)
    assert ("set_zero",) not in controller.calls
    qtbot.mouseClick(window.set_zero_button, Qt.LeftButton)
    assert [call[0] for call in controller.calls].count("set_zero") == 1
    assert "人工目视对准机械 0°" in questions[0][1]
    assert "软件零点" in questions[0][1]

    controller.emit_snapshot(
        connected_snapshot(
            status=status_snapshot(run_state=RunState.AUTO_RUNNING, run_status=RunStatus.RUNNING)
        )
    )
    window._request_set_zero()
    assert len(questions) == 2
    assert [call[0] for call in controller.calls].count("set_zero") == 1


def test_command_exceptions_are_inline_and_do_not_crash(qtbot, tmp_path: Path) -> None:
    window, _factory, controller = make_connected_window(qtbot, tmp_path)
    controller.failures["start"] = RuntimeError("启动被控制器拒绝")

    qtbot.mouseClick(window.start_button, Qt.LeftButton)

    assert "启动被控制器拒绝" in window.message_label.text()
    assert not window.start_button.isEnabled()
    assert window.stop_button.isEnabled()

    controller.emit_snapshot(connected_snapshot())
    assert not window.start_button.isEnabled()
    window.direction_combo.setCurrentIndex(1)
    assert window.start_button.isEnabled()


def test_worker_callback_reaches_widgets_only_on_gui_thread(qtbot, tmp_path: Path) -> None:
    window, _factory, controller = make_connected_window(qtbot, tmp_path)
    gui_thread_id = get_ident()
    update = connected_snapshot(
        status=status_snapshot(actual_position_deg=123.0, heartbeat_echo=99)
    )

    worker = Thread(target=controller.emit_snapshot, args=(update,))
    worker.start()
    worker.join()

    qtbot.waitUntil(lambda: window.actual_position_label.text() == "+123.000°")
    assert window.last_snapshot_thread_id == gui_thread_id
    assert isinstance(window._bridge, ControllerBridge)


def test_start_requires_every_ui_interlock_and_unknowns_fail_closed(qtbot, tmp_path: Path) -> None:
    window = make_window(qtbot, tmp_path)
    cases = (
        ControllerSnapshot(),
        connected_snapshot(status=status_snapshot(status_flags=0x0002)),
        connected_snapshot(status=status_snapshot(status_flags=0x0001)),
        connected_snapshot(status=status_snapshot(status_flags=0x000B)),
        connected_snapshot(download_pending=True),
        connected_snapshot(status=status_snapshot(run_state=RunState.ZERO_REQUIRED)),
        connected_snapshot(status=status_snapshot(run_state=123)),
    )
    for snapshot in cases:
        window.apply_snapshot(snapshot)
        assert not window.start_button.isEnabled()
        assert window.stop_button.isEnabled()


def test_disconnect_space_stop_and_controller_errors_remain_non_modal(qtbot, tmp_path: Path) -> None:
    window, _factory, controller = make_connected_window(qtbot, tmp_path)
    controller.failures["stop"] = RuntimeError("软件停止被拒绝")

    window._software_stop_shortcut.activated.emit()
    assert "软件停止被拒绝" in window.message_label.text()
    assert window.stop_button.isEnabled()

    controller.emit_snapshot(
        connected_snapshot(
            status=status_snapshot(run_state=RunState.MANUAL_RUNNING, run_status=RunStatus.RUNNING)
        )
    )
    assert window.disconnect_button.isEnabled()
    qtbot.mouseClick(window.disconnect_button, Qt.LeftButton)
    assert ("disconnect",) in controller.calls


def test_stop_cancels_local_start_pending_lock_without_disabling_stop(qtbot, tmp_path: Path) -> None:
    window, _factory, controller = make_connected_window(qtbot, tmp_path)
    qtbot.mouseClick(window.start_button, Qt.LeftButton)
    assert not window.mode_combo.isEnabled()

    window._software_stop_shortcut.activated.emit()
    controller.emit_snapshot(connected_snapshot())

    assert ("stop",) in controller.calls
    assert window.mode_combo.isEnabled()
    assert window.stop_button.isEnabled()


def test_disconnect_clears_local_start_pending_and_recovers_connection_controls(
    qtbot, tmp_path: Path
) -> None:
    window, _factory, controller = make_connected_window(qtbot, tmp_path)
    qtbot.mouseClick(window.start_button, Qt.LeftButton)
    assert window._start_pending
    assert [call[0] for call in controller.calls].count("start") == 1

    qtbot.mouseClick(window.disconnect_button, Qt.LeftButton)
    assert not window._start_pending
    controller.emit_snapshot(ControllerSnapshot())
    qtbot.waitUntil(lambda: window.connect_button.isEnabled())

    assert window.ip_edit.isEnabled()
    assert window.connect_button.isEnabled()
    assert not window.disconnect_button.isEnabled()
    assert [call[0] for call in controller.calls].count("start") == 1


def test_open_data_directory_uses_configured_path(qtbot, tmp_path: Path, monkeypatch) -> None:
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url) or True)
    window = make_window(qtbot, tmp_path)

    qtbot.mouseClick(window.open_data_button, Qt.LeftButton)

    assert len(opened) == 1
    assert Path(opened[0].toLocalFile()) == tmp_path.resolve()


def test_close_unsubscribes_and_uses_bounded_shutdown(qtbot, tmp_path: Path) -> None:
    window, _factory, controller = make_connected_window(qtbot, tmp_path)

    window.close()

    assert controller.unsubscribe_count == 3
    assert [call for call in controller.calls if call[0] == "shutdown"] == [("shutdown", 2.0)]


def test_close_reports_controller_stopped_inline_and_still_closes(qtbot, tmp_path: Path) -> None:
    window, _factory, controller = make_connected_window(qtbot, tmp_path)
    controller.failures["shutdown"] = ControllerStopped("控制器线程未能停止")

    window.close()

    assert "控制器线程未能停止" in window.message_label.text()
    assert not window.isVisible()


def test_queued_old_worker_snapshot_cannot_overwrite_replacement_controller(
    qtbot, tmp_path: Path
) -> None:
    window, factory, first = make_connected_window(qtbot, tmp_path)
    first.emit_snapshot(ControllerSnapshot())
    stale = connected_snapshot(
        status=status_snapshot(
            run_state=RunState.READY,
            run_status=RunStatus.IDLE,
            actual_position_deg=111.0,
        )
    )
    worker = Thread(target=first.emit_snapshot, args=(stale,))
    worker.start()
    worker.join()

    window.ip_edit.setText("192.0.2.99")
    window._request_connect()
    second = factory.controllers[1]
    second.emit_snapshot(
        connected_snapshot(
            status=status_snapshot(
                run_state=RunState.AUTO_RUNNING,
                run_status=RunStatus.RUNNING,
                actual_position_deg=222.0,
            )
        )
    )
    QApplication.processEvents()

    assert window.actual_position_label.text() == "+222.000°"
    assert window.run_state_label.text() == "自动运行"
    assert not window.mode_combo.isEnabled()
    assert not window.set_zero_button.isEnabled()
    assert not window.power_button.isEnabled()


def test_queued_worker_callbacks_are_ignored_after_close(qtbot, tmp_path: Path) -> None:
    window, _factory, controller = make_connected_window(qtbot, tmp_path)
    before_position = window.actual_position_label.text()
    before_thread = window.last_snapshot_thread_id
    stale = connected_snapshot(status=status_snapshot(actual_position_deg=333.0))
    worker = Thread(target=controller.emit_snapshot, args=(stale,))
    worker.start()
    worker.join()

    window.close()
    QApplication.processEvents()

    assert window.actual_position_label.text() == before_position
    assert window.last_snapshot_thread_id == before_thread
    assert not window.isVisible()


def test_main_is_import_safe_and_does_not_construct_network_client_before_connect(
    tmp_path: Path, monkeypatch
) -> None:
    import turntable_control.main as app_main

    events: list[tuple[object, ...]] = []

    class FakeApplication:
        @staticmethod
        def instance():
            return None

        def __init__(self, argv) -> None:
            events.append(("application", tuple(argv)))

        def setApplicationName(self, name: str) -> None:
            events.append(("application-name", name))

        def exec(self) -> int:
            events.append(("exec",))
            return 23

    class FakeWindow:
        def __init__(self, factory, data_dir, *, initial_ip: str = "") -> None:
            events.append(("window", factory, Path(data_dir), initial_ip))

        def show(self) -> None:
            events.append(("show",))

    class ForbiddenNetworkClient:
        def __init__(self, *args, **kwargs) -> None:
            raise AssertionError("network client was constructed before Connect")

    monkeypatch.setattr(app_main, "QApplication", FakeApplication)
    monkeypatch.setattr(app_main, "MainWindow", FakeWindow)
    monkeypatch.setattr(app_main, "TurntableModbusClient", ForbiddenNetworkClient)

    result = app_main.main(
        ["--plc-ip", "192.0.2.44", "--data-dir", str(tmp_path / "operator-data")]
    )

    assert result == 23
    assert [event[0] for event in events] == ["application", "application-name", "window", "show", "exec"]
    window_event = events[2]
    assert callable(window_event[1])
    assert window_event[2] == tmp_path / "operator-data"
    assert window_event[3] == "192.0.2.44"
