"""PySide6 operator interface for the turntable controller."""

from __future__ import annotations

from pathlib import Path
from ipaddress import IPv4Address, AddressValueError
from threading import get_ident
from typing import Callable

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QCloseEvent, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .controller import ControllerSnapshot, STATUS_BUFFER_READY, STATUS_POWERED, STATUS_ZERO_VALID
from .domain import Direction, Mode, RunState, RunStatus, SPEEDS_DEG_S


_MOTION_ACTIVE_STATES = frozenset(
    (RunState.MANUAL_RUNNING, RunState.AUTO_RUNNING, RunState.STOPPING)
)
_RUN_STATE_LABELS = {
    RunState.INITIALIZING: "初始化中",
    RunState.ZERO_REQUIRED: "需要设零",
    RunState.READY: "就绪",
    RunState.MANUAL_RUNNING: "手动运行",
    RunState.AUTO_RUNNING: "自动运行",
    RunState.STOPPING: "正在停止",
    RunState.AUTO_ABORTED: "自动中止",
    RunState.FAULT: "故障",
}
_RUN_STATUS_LABELS = {
    RunStatus.IDLE: "空闲",
    RunStatus.RUNNING: "运行中",
    RunStatus.COMPLETED: "已完成",
    RunStatus.MANUAL_STOPPED: "手动停止",
    RunStatus.AUTOMATIC_ABORTED: "自动中止",
    RunStatus.COMMUNICATION_ABORTED: "通信中止",
    RunStatus.FAULTED: "故障终止",
}


class ControllerBridge(QObject):
    """Marshal controller worker callbacks into the Qt object's GUI thread."""

    snapshot_received = Signal(int, object)
    error_received = Signal(int, str)
    saved_received = Signal(int, object)

    def __init__(self, controller: object, generation: int) -> None:
        super().__init__()
        self._generation = generation
        self._unsubscribers: list[Callable[[], None]] = []
        registrations = (
            (controller.on_snapshot, self._forward_snapshot),  # type: ignore[attr-defined]
            (controller.on_error, self._forward_error),  # type: ignore[attr-defined]
            (controller.on_run_saved, self._forward_saved),  # type: ignore[attr-defined]
        )
        try:
            for register, callback in registrations:
                self._unsubscribers.append(register(callback))
        except Exception:
            self._unsubscribe_all(suppress=True)
            raise

    def _forward_snapshot(self, snapshot: object) -> None:
        self.snapshot_received.emit(self._generation, snapshot)

    def _forward_error(self, message: str) -> None:
        self.error_received.emit(self._generation, message)

    def _forward_saved(self, path: object) -> None:
        self.saved_received.emit(self._generation, path)

    def set_generation(self, generation: int) -> None:
        self._generation = generation

    def close(self) -> None:
        self._unsubscribe_all(suppress=False)

    def _unsubscribe_all(self, *, suppress: bool) -> None:
        unsubscribers, self._unsubscribers = self._unsubscribers, []
        first_error: Exception | None = None
        for unsubscribe in reversed(unsubscribers):
            try:
                unsubscribe()
            except Exception as error:
                if first_error is None:
                    first_error = error
        if first_error is not None and not suppress:
            raise first_error


class MainWindow(QMainWindow):
    """Main operator window backed exclusively by a controller instance."""

    def __init__(
        self,
        controller_factory: Callable[[str], object],
        data_dir: str | Path,
        *,
        initial_ip: str = "",
        simulator_mode: bool = False,
    ) -> None:
        super().__init__()
        self._controller_factory = controller_factory
        self._data_dir = Path(data_dir)
        self._simulator_mode = simulator_mode
        self._controller: object | None = None
        self._controller_ip: str | None = None
        self._bridge: ControllerBridge | None = None
        self._controller_generation = 0
        self._snapshot = ControllerSnapshot()
        self._start_pending = False
        self._local_command_error = False
        self._saved_path: Path | None = None
        self.last_snapshot_thread_id: int | None = None
        self._closing = False
        self._build_ui(initial_ip)
        if simulator_mode:
            self.setWindowTitle("天线测试转台控制 — 模拟器（无 PLC）")
            self.ip_edit.setReadOnly(True)
            self.ip_edit.setToolTip("本地软件模拟，不访问 PLC 或网络")
            self.connection_status_label.setText("模拟器未启动")
            self.ethercat_status_label.setText("模拟器：无 EtherCAT / 无真实运动")
        self._software_stop_shortcut = QShortcut(QKeySequence(Qt.Key_Space), self)
        self._software_stop_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self._software_stop_shortcut.activated.connect(self._request_stop)

    def _build_ui(self, initial_ip: str) -> None:
        self.setWindowTitle("天线测试转台控制")
        root = QWidget(self)
        layout = QVBoxLayout(root)
        self.setCentralWidget(root)

        connection = QGroupBox("连接与设备状态")
        connection_layout = QGridLayout(connection)
        self.ip_edit = QLineEdit(initial_ip)
        self.ip_edit.setPlaceholderText("请输入 PLC IPv4 地址")
        self.connect_button = QPushButton("连接")
        self.disconnect_button = QPushButton("断开")
        self.disconnect_button.setEnabled(False)
        connection_layout.addWidget(QLabel("PLC IPv4"), 0, 0)
        connection_layout.addWidget(self.ip_edit, 0, 1)
        connection_layout.addWidget(self.connect_button, 0, 2)
        connection_layout.addWidget(self.disconnect_button, 0, 3)
        self.connection_status_label = QLabel("离线")
        self.run_state_label = QLabel("未知")
        self.heartbeat_label = QLabel("—")
        self.power_status_label = QLabel("未上电 / 未就绪")
        self.fault_label = QLabel("0")
        self.ethercat_status_label = QLabel("由 PLC 轴状态综合判断 / 现场待验证")
        connection_layout.addWidget(QLabel("PLC 状态"), 1, 0)
        connection_layout.addWidget(self.connection_status_label, 1, 1)
        connection_layout.addWidget(QLabel("心跳"), 1, 2)
        connection_layout.addWidget(self.heartbeat_label, 1, 3)
        connection_layout.addWidget(QLabel("伺服状态"), 2, 0)
        connection_layout.addWidget(self.power_status_label, 2, 1)
        connection_layout.addWidget(QLabel("故障码"), 2, 2)
        connection_layout.addWidget(self.fault_label, 2, 3)
        connection_layout.addWidget(QLabel("EtherCAT"), 3, 0)
        connection_layout.addWidget(self.ethercat_status_label, 3, 1, 1, 3)
        connection_layout.addWidget(QLabel("PLC 运行状态"), 4, 0)
        connection_layout.addWidget(self.run_state_label, 4, 1, 1, 3)
        layout.addWidget(connection)

        angles = QGroupBox("角度状态")
        angle_layout = QGridLayout(angles)
        self.actual_position_label = QLabel("—")
        self.target_position_label = QLabel("—")
        self.actual_velocity_label = QLabel("—")
        self.range_label = QLabel("-360° ～ +360°")
        self.zero_status_label = QLabel("未知")
        self.limit_label = QLabel("未知")
        angle_fields = (
            ("实际位置", self.actual_position_label),
            ("目标位置", self.target_position_label),
            ("实际速度", self.actual_velocity_label),
            ("软件范围", self.range_label),
            ("零点有效", self.zero_status_label),
            ("限位状态", self.limit_label),
        )
        for index, (title, value) in enumerate(angle_fields):
            row, column = divmod(index, 3)
            angle_layout.addWidget(QLabel(title), row * 2, column)
            angle_layout.addWidget(value, row * 2 + 1, column)
        layout.addWidget(angles)

        settings = QGroupBox("运行设置")
        settings_layout = QHBoxLayout(settings)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("手动", Mode.MANUAL)
        self.mode_combo.addItem("自动", Mode.AUTO)
        self.direction_combo = QComboBox()
        self.direction_combo.addItem("正转", Direction.CW)
        self.direction_combo.addItem("反转", Direction.CCW)
        self.speed_combo = QComboBox()
        for speed in SPEEDS_DEG_S:
            self.speed_combo.addItem(f"{speed:g}°/s", speed)
        for title, widget in (
            ("模式", self.mode_combo),
            ("方向", self.direction_combo),
            ("速度", self.speed_combo),
        ):
            settings_layout.addWidget(QLabel(title))
            settings_layout.addWidget(widget)
        layout.addWidget(settings)

        operations = QGroupBox("操作与数据记录")
        operations_layout = QGridLayout(operations)
        self.power_button = QPushButton("伺服上电/下电")
        self.start_button = QPushButton("启动")
        self.stop_button = QPushButton("停止（软件）")
        self.stop_button.setStyleSheet(
            "QPushButton { background-color: #c62828; color: white; font-weight: bold; }"
        )
        self.stop_button.setToolTip("普通软件停止，不能替代实体急停")
        self.set_zero_button = QPushButton("当前位置设零")
        self.reset_button = QPushButton("报警复位")
        self.retry_button = QPushButton("重试下载")
        self.retry_button.setVisible(False)
        self.open_data_button = QPushButton("打开数据目录")
        for column, button in enumerate(
            (
                self.power_button,
                self.start_button,
                self.stop_button,
                self.set_zero_button,
                self.reset_button,
                self.retry_button,
                self.open_data_button,
            )
        ):
            operations_layout.addWidget(button, 0, column)
        self.test_id_label = QLabel("—")
        self.event_count_label = QLabel("0")
        self.run_status_label = QLabel("未知")
        self.csv_path_label = QLabel("—")
        self.csv_status_label = QLabel("未保存")
        data_fields = (
            ("测试编号", self.test_id_label),
            ("事件数量", self.event_count_label),
            ("运行状态", self.run_status_label),
            ("CSV 路径", self.csv_path_label),
            ("保存状态", self.csv_status_label),
        )
        for row, (title, value) in enumerate(data_fields, start=1):
            operations_layout.addWidget(QLabel(title), row, 0)
            operations_layout.addWidget(value, row, 1, 1, 6)
        self.message_label = QLabel("")
        self.message_label.setWordWrap(True)
        operations_layout.addWidget(QLabel("操作提示"), len(data_fields) + 1, 0)
        operations_layout.addWidget(self.message_label, len(data_fields) + 1, 1, 1, 6)
        layout.addWidget(operations)

        advanced = QGroupBox("高级参数")
        advanced_layout = QVBoxLayout(advanced)
        self.advanced_toggle = QPushButton("展开高级参数")
        self.advanced_toggle.setCheckable(True)
        self.advanced_panel = QWidget()
        advanced_form = QFormLayout(self.advanced_panel)
        self.ratio_value = self._readonly_value("50:1")
        self.acceleration_value = self._readonly_value("5°/s²")
        self.deceleration_value = self._readonly_value("5°/s²")
        self.stop_deceleration_value = self._readonly_value("10°/s²")
        self.backlash_value = self._readonly_value("0°")
        advanced_form.addRow("总减速比", self.ratio_value)
        advanced_form.addRow("加速度", self.acceleration_value)
        advanced_form.addRow("减速度", self.deceleration_value)
        advanced_form.addRow("软件停止减速度", self.stop_deceleration_value)
        advanced_form.addRow("反向间隙补偿", self.backlash_value)
        advanced_layout.addWidget(self.advanced_toggle)
        advanced_layout.addWidget(self.advanced_panel)
        self.advanced_panel.setVisible(False)
        self.advanced_toggle.toggled.connect(self.advanced_panel.setVisible)
        layout.addWidget(advanced)

        self.start_button.setEnabled(False)
        self.power_button.setEnabled(False)
        self.set_zero_button.setEnabled(False)
        self.reset_button.setEnabled(False)
        self.retry_button.setEnabled(False)
        self.stop_button.clicked.connect(self._request_stop)
        self.connect_button.clicked.connect(self._request_connect)
        self.disconnect_button.clicked.connect(self._request_disconnect)
        self.start_button.clicked.connect(self._request_start)
        self.set_zero_button.clicked.connect(self._request_set_zero)
        self.power_button.clicked.connect(lambda: self._invoke_controller("toggle_power"))
        self.reset_button.clicked.connect(lambda: self._invoke_controller("reset_alarm"))
        self.retry_button.clicked.connect(lambda: self._invoke_controller("retry_download"))
        self.open_data_button.clicked.connect(self._open_data_directory)
        self.ip_edit.textChanged.connect(self._clear_command_error)
        self.mode_combo.currentIndexChanged.connect(self._clear_command_error)
        self.direction_combo.currentIndexChanged.connect(self._clear_command_error)
        self.speed_combo.currentIndexChanged.connect(self._clear_command_error)

    @staticmethod
    def _readonly_value(text: str) -> QLabel:
        value = QLabel(text)
        value.setEnabled(False)
        value.setToolTip("第一阶段参数只读，不可编辑；需与 PLC 和轴缩放统一配置。")
        return value

    def _show_message(self, message: str) -> None:
        self._local_command_error = bool(message)
        self.message_label.setText(message)
        self.statusBar().showMessage(message)
        self._refresh_controls()

    def _clear_command_error(self, *_args: object) -> None:
        if not self._local_command_error:
            return
        self._local_command_error = False
        self.message_label.clear()
        self.statusBar().clearMessage()
        self._refresh_controls()

    def _request_connect(self) -> None:
        ip_text = self.ip_edit.text().strip()
        if self._simulator_mode:
            plc_ip = "SIMULATOR"
        else:
            try:
                plc_ip = str(IPv4Address(ip_text))
            except AddressValueError:
                self._show_message("请输入有效的 PLC IPv4 地址")
                return
        if self._controller is not None and self._controller_ip != plc_ip:
            if self._snapshot.connected:
                self._show_message("请先断开当前 PLC，再更换 IPv4 地址")
                return
            if not self._dispose_controller():
                return
        first_connect = self._controller is None
        if first_connect:
            controller: object | None = None
            try:
                controller = self._controller_factory(plc_ip)
                generation = self._invalidate_generation()
                bridge = ControllerBridge(controller, generation)
            except Exception as error:
                message = str(error)
                if controller is not None:
                    cleanup_error = self._shutdown_uninstalled_controller(controller)
                    if cleanup_error:
                        message = f"{message}；清理失败：{cleanup_error}"
                self._show_message(message)
                return
            self._controller = controller
            self._controller_ip = plc_ip
            self._bridge = bridge
            bridge.snapshot_received.connect(self._apply_bridge_snapshot)
            bridge.error_received.connect(self._apply_bridge_error)
            bridge.saved_received.connect(self._apply_bridge_saved)
            snapshot = getattr(controller, "snapshot", None)
            if isinstance(snapshot, ControllerSnapshot):
                self.apply_snapshot(snapshot)
        try:
            self._controller.connect()  # type: ignore[union-attr]
            if first_connect:
                self._controller.start_background()  # type: ignore[union-attr]
        except Exception as error:
            message = str(error)
            if first_connect:
                cleanup_error = self._discard_failed_first_controller()
                if cleanup_error:
                    message = f"{message}；清理失败：{cleanup_error}"
            self._show_message(message)
            return
        self._local_command_error = False
        self._show_message("正在启动本地模拟器…" if self._simulator_mode else "正在连接 PLC…")
        self._local_command_error = False
        self._refresh_controls()

    def _request_disconnect(self) -> None:
        if self._invoke_controller("disconnect"):
            self._start_pending = False
            self._refresh_controls()

    def _request_start(self) -> None:
        if self._controller is None:
            self._show_message("PLC未连接")
            return
        mode = self.mode_combo.currentData()
        direction = self.direction_combo.currentData()
        speed = self.speed_combo.currentData()
        try:
            self._controller.start(mode, direction, speed)  # type: ignore[attr-defined]
        except Exception as error:
            self._start_pending = False
            self._show_message(str(error))
            return
        self._start_pending = True
        self._local_command_error = False
        self.message_label.setText("启动命令等待 PLC 确认")
        self.statusBar().showMessage("启动命令等待 PLC 确认")
        self._saved_path = None
        self.csv_path_label.setText("—")
        self.csv_status_label.setText("未保存")
        self._refresh_controls()

    def _request_stop(self) -> None:
        if self._controller is None:
            self._show_message("PLC未连接，软件停止命令未发送")
            return
        try:
            self._controller.stop()  # type: ignore[attr-defined]
        except Exception as error:
            self._show_message(str(error))
            return
        self._start_pending = False
        self._local_command_error = False
        self.message_label.setText("软件停止命令已提交，等待 PLC 确认")
        self.statusBar().showMessage("软件停止命令已提交，等待 PLC 确认")
        self._refresh_controls()

    def _request_set_zero(self) -> None:
        if self._motion_active() or self._start_pending:
            self._show_message("运行或停止过程中不能设置零点")
            return
        if self._controller is None or not self._snapshot.connected:
            self._show_message("PLC未连接")
            return
        answer = QMessageBox.question(
            self,
            "确认当前位置设零",
            "请先人工目视对准机械 0°。确认后将当前位置写为软件零点，是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._invoke_controller("set_zero")

    def _invoke_controller(self, method_name: str) -> bool:
        if self._controller is None:
            self._show_message("PLC未连接")
            return False
        try:
            getattr(self._controller, method_name)()
        except Exception as error:
            self._show_message(str(error))
            return False
        self._local_command_error = False
        self.message_label.setText("命令已提交，等待 PLC 确认")
        self.statusBar().showMessage("命令已提交，等待 PLC 确认")
        self._refresh_controls()
        return True

    def _motion_active(self) -> bool:
        status = self._snapshot.status
        return (
            status is not None
            and type(status.run_state) is RunState
            and status.run_state in _MOTION_ACTIVE_STATES
        )

    def _open_data_directory(self) -> None:
        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._data_dir.resolve())))
            if not opened:
                self._show_message("无法打开数据目录")
        except OSError as error:
            self._show_message(f"无法打开数据目录：{error}")

    def _apply_error(self, message: str) -> None:
        self._start_pending = False
        self._show_message(message)

    def _apply_bridge_snapshot(self, generation: int, snapshot: object) -> None:
        if self._bridge_event_is_current(generation):
            self.apply_snapshot(snapshot)  # type: ignore[arg-type]

    def _apply_bridge_error(self, generation: int, message: str) -> None:
        if self._bridge_event_is_current(generation):
            self._apply_error(message)

    def _apply_bridge_saved(self, generation: int, path: object) -> None:
        if self._bridge_event_is_current(generation):
            self.apply_saved_path(path)  # type: ignore[arg-type]

    def _bridge_event_is_current(self, generation: int) -> bool:
        return not self._closing and generation == self._controller_generation

    def _invalidate_generation(self) -> int:
        self._controller_generation += 1
        return self._controller_generation

    def apply_snapshot(self, snapshot: ControllerSnapshot) -> None:
        """Render one immutable controller snapshot on the GUI thread."""
        if not isinstance(snapshot, ControllerSnapshot):
            raise TypeError("snapshot must be a ControllerSnapshot")
        self.last_snapshot_thread_id = get_ident()
        self._snapshot = snapshot
        if not snapshot.connected:
            self._start_pending = False
        if self._start_pending and (
            snapshot.last_error is not None
            or (
                snapshot.status is not None
                and type(snapshot.status.run_state) is RunState
                and snapshot.status.run_state in _MOTION_ACTIVE_STATES
            )
        ):
            self._start_pending = False
        self._local_command_error = self._local_command_error or snapshot.last_error is not None
        if snapshot.last_error:
            self.message_label.setText(snapshot.last_error)
            self.statusBar().showMessage(snapshot.last_error)
        status = snapshot.status
        self.connection_status_label.setText("在线" if snapshot.connected else "离线")
        if status is None:
            self.run_state_label.setText("未知")
            self.heartbeat_label.setText("—")
            self.power_status_label.setText("未上电 / 未就绪")
            self.fault_label.setText("—")
            self.actual_position_label.setText("—")
            self.target_position_label.setText("—")
            self.actual_velocity_label.setText("—")
            self.zero_status_label.setText("未知")
            self.limit_label.setText("未知")
            self.run_status_label.setText("未知")
            self.event_count_label.setText("0")
        else:
            self.run_state_label.setText(self._enum_label(status.run_state, _RUN_STATE_LABELS))
            self.heartbeat_label.setText(str(status.heartbeat_echo))
            powered = bool(status.status_flags & STATUS_POWERED)
            self.power_status_label.setText("已上电 / 已就绪" if powered else "未上电 / 未就绪")
            self.fault_label.setText(str(status.fault_code))
            self.actual_position_label.setText(f"{status.actual_position_deg:+.3f}°")
            self.target_position_label.setText(f"{status.target_position_deg:+.3f}°")
            self.actual_velocity_label.setText(f"{status.actual_velocity_deg_s:+.3f}°/s")
            self.zero_status_label.setText(
                "有效" if status.status_flags & STATUS_ZERO_VALID else "无效"
            )
            if status.actual_position_deg >= 360.0:
                limit_text = "正向限位"
            elif status.actual_position_deg <= -360.0:
                limit_text = "反向限位"
            else:
                limit_text = "范围内"
            self.limit_label.setText(limit_text)
            self.run_status_label.setText(self._enum_label(status.run_status, _RUN_STATUS_LABELS))
            self.event_count_label.setText(str(status.event_count))
        self.test_id_label.setText(snapshot.active_test_id or "—")
        self._refresh_csv_status()
        self._refresh_controls()

    def apply_saved_path(self, path: str | Path) -> None:
        """Show a durable CSV path only after the saved callback fires."""
        self._saved_path = Path(path)
        self.csv_path_label.setText(str(self._saved_path))
        self._refresh_csv_status()

    def _refresh_csv_status(self) -> None:
        if self._saved_path is not None:
            text = "已保存，等待 PLC 确认" if self._snapshot.download_pending else "已保存"
        else:
            text = "待下载或保存" if self._snapshot.download_pending else "未保存"
        self.csv_status_label.setText(text)

    @staticmethod
    def _enum_label(raw: object, labels: dict[object, str]) -> str:
        enum_type = type(next(iter(labels)))
        label = labels.get(raw) if type(raw) is enum_type else None
        return label if label is not None else f"未知（{raw}）"

    def _refresh_controls(self) -> None:
        snapshot = self._snapshot
        status = snapshot.status
        run_state = None if status is None else status.run_state
        run_status = None if status is None else status.run_status
        exact_known_status = type(run_state) is RunState and type(run_status) is RunStatus
        unknown_connected = snapshot.connected and not exact_known_status
        motion_active = type(run_state) is RunState and run_state in _MOTION_ACTIVE_STATES
        locked = motion_active or self._start_pending or unknown_connected
        self.ip_edit.setEnabled(not locked)
        self.connect_button.setEnabled(not locked and not snapshot.connected)
        self.disconnect_button.setEnabled(snapshot.connected)
        self.mode_combo.setEnabled(not locked)
        self.direction_combo.setEnabled(not locked)
        self.speed_combo.setEnabled(not locked)
        connected_action = snapshot.connected and status is not None and not locked
        self.set_zero_button.setEnabled(connected_action)
        self.power_button.setEnabled(connected_action)
        self.reset_button.setEnabled(connected_action)
        self.advanced_toggle.setEnabled(not locked)
        self.open_data_button.setEnabled(not unknown_connected)
        self.retry_button.setVisible(snapshot.download_pending)
        self.retry_button.setEnabled(snapshot.connected and snapshot.download_pending and not locked)
        exact_safe_idle = (
            status is not None
            and status.run_state is RunState.READY
            and status.run_status is RunStatus.IDLE
        )
        start_allowed = (
            snapshot.connected
            and exact_safe_idle
            and bool(status.status_flags & STATUS_ZERO_VALID)
            and bool(status.status_flags & STATUS_POWERED)
            and not bool(status.status_flags & STATUS_BUFFER_READY)
            and not snapshot.download_pending
            and not self._local_command_error
            and not locked
        )
        self.start_button.setEnabled(start_allowed)
        self.stop_button.setEnabled(True)

    def _dispose_controller(self) -> bool:
        self._invalidate_generation()
        bridge = self._bridge
        controller = self._controller
        if controller is not None:
            try:
                controller.shutdown(timeout=2.0)  # type: ignore[attr-defined]
            except Exception as error:
                if bridge is not None:
                    bridge.set_generation(self._invalidate_generation())
                self._show_message(str(error))
                return False
        close_error: Exception | None = None
        if bridge is not None:
            try:
                bridge.close()
            except Exception as error:
                close_error = error
        self._bridge = None
        self._controller = None
        self._controller_ip = None
        self._snapshot = ControllerSnapshot()
        self.apply_snapshot(self._snapshot)
        if close_error is not None:
            self._show_message(str(close_error))
        return True

    @staticmethod
    def _shutdown_uninstalled_controller(controller: object) -> str | None:
        try:
            controller.shutdown(timeout=2.0)  # type: ignore[attr-defined]
        except Exception as error:
            return str(error)
        return None

    def _discard_failed_first_controller(self) -> str | None:
        self._invalidate_generation()
        bridge = self._bridge
        controller = self._controller
        if controller is not None:
            try:
                controller.shutdown(timeout=2.0)  # type: ignore[attr-defined]
            except Exception as error:
                if bridge is not None:
                    bridge.set_generation(self._invalidate_generation())
                return str(error)
        close_error: str | None = None
        if bridge is not None:
            try:
                bridge.close()
            except Exception as error:
                close_error = str(error)
        self._bridge = None
        self._controller = None
        self._controller_ip = None
        self._snapshot = ControllerSnapshot()
        self.apply_snapshot(self._snapshot)
        return close_error

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API name
        if not self._closing:
            self._closing = True
            self._invalidate_generation()
            bridge, self._bridge = self._bridge, None
            controller, self._controller = self._controller, None
            self._controller_ip = None
            if bridge is not None:
                bridge.close()
            if controller is not None:
                try:
                    controller.shutdown(timeout=2.0)  # type: ignore[attr-defined]
                except Exception as error:
                    self._show_message(str(error))
        event.accept()
