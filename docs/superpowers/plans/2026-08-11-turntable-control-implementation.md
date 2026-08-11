# 天线转台 PLC 与 Windows 控制系统 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付一套可在 Easy521-0808TN + SV660N 上运行的转台控制方案，以及可在 Windows 上控制、逐度记录时间戳并导出 CSV 的桌面程序。

**Architecture:** PLC 通过 EtherCAT 独立完成使能、相对定位、平滑停止、软件限位和逐度事件缓存；Windows 程序通过 PLC 默认开启的 Modbus TCP 服务器读写 D 寄存器。纯规则、协议编解码、时间换算和 CSV 先以 Python 自动化测试覆盖，再接入 PySide6 界面；AutoShop 专有工程由可粘贴的 LiteST 源码、寄存器表和逐步组态说明组成。

**Tech Stack:** Easy521-0808TN、AutoShop 4.12、PLCopen MC 指令、Modbus TCP 502、Python 3.12、pymodbus 3.14.0、PySide6 6.11.1、pytest 8.x、PyInstaller 6.x。

## Global Constraints

- 总减速比默认 50:1；转台工程单位为度、度/秒和度/秒平方。
- 速度档固定为 1、2、4、5、10°/s；初始加减速度 5°/s²，软件停止减速度 10°/s²。
- 人工设零，只允许轴停止且无故障时执行；连续位置范围为 -360°～+360°。
- 手动和自动均支持正反转；单次运行不超过一圈，自动模式严格运行 ±360°。
- 电脑心跳丢失超过 1 秒时，手动和自动均由 PLC 平滑停止并标记中止。
- 每次运行记录经过的整数行程角度；自动完成必须有 1～360 共 360 条记录。
- 时间字段记录分辨率为 1 ms，绝对时间换算精度必须通过测试报告说明，不把分辨率等同于精度。
- 没有实体急停时只允许空载、人员远离和 1°/s 初始调试；电脑停止按钮不得标注为实体急停。
- 所有源码、说明、测试和工作日志均保存在本工作区，不覆盖用户已有 CAD、PDF 和临时资料。

---

## File Structure

```text
.gitignore                                  只忽略生成物和本项目虚拟环境
README.md                                   项目入口、安装和安全说明
plc/README.md                               AutoShop 新建、粘贴、组态、下载和调试步骤
plc/register-map.md                         Modbus D 寄存器唯一契约
plc/src/Turntable_Constants.st              常量、状态和故障码
plc/src/Turntable_RegisterCodec.st          DINT 与两个 D 寄存器的显式编解码
plc/src/FB_DegreeLogger.st                  逐度跨越检测与 360 条事件缓存
plc/src/FB_TurntableControl.st              运动状态机、限位、心跳和命令确认
plc/src/PRG_MAIN.st                         MC 指令调用与状态寄存器映射
pc/pyproject.toml                           固定依赖、pytest 和打包入口
pc/src/turntable_control/domain.py          枚举、数据类和运动规则
pc/src/turntable_control/registers.py       寄存器地址、位标志和 32 位编解码
pc/src/turntable_control/modbus_client.py   pymodbus 同步客户端适配器
pc/src/turntable_control/time_sync.py       PLC 时钟到 Windows epoch 毫秒换算
pc/src/turntable_control/csv_store.py       原子 CSV 写入和恢复
pc/src/turntable_control/controller.py      心跳、命令序号、轮询和会话协调
pc/src/turntable_control/simulator.py       无硬件 Modbus/运动模拟器
pc/src/turntable_control/ui.py              PySide6 主窗口
pc/src/turntable_control/main.py            命令行入口
pc/tests/                                   纯单元测试和模拟端到端测试
scripts/setup.ps1                           创建虚拟环境并安装固定依赖
scripts/run-app.ps1                         启动桌面程序
scripts/run-simulator.ps1                   启动本地 PLC 模拟器
docs/worklog/2026-08-11-development-log.md  连续工作日志，供 Open WebUI 导入
docs/verification-report.md                 最终测试证据与硬件待验证项
```

---

### Task 1: 建立可重复的 Python 工程与工作日志

**Files:**
- Create: `.gitignore`
- Create: `README.md`
- Create: `pc/pyproject.toml`
- Create: `pc/src/turntable_control/__init__.py`
- Create: `pc/tests/test_smoke.py`
- Create: `scripts/setup.ps1`
- Create: `docs/worklog/2026-08-11-development-log.md`

**Interfaces:**
- Produces: 可编辑安装的 `turntable-control` 包、`turntable-control` 控制台入口和统一测试命令。

- [ ] **Step 1: 初始化 Git，并只忽略生成物**

```gitignore
.venv/
__pycache__/
*.py[cod]
.pytest_cache/
.coverage
build/
dist/
*.spec
data/*.tmp
```

Run: `git init`

Expected: 当前目录出现 `.git`，用户已有文件保持不变。

- [ ] **Step 2: 写入固定依赖和入口**

```toml
[project]
name = "turntable-control"
version = "0.1.0"
requires-python = ">=3.12,<3.13"
dependencies = ["pymodbus==3.14.0", "PySide6==6.11.1"]

[project.optional-dependencies]
dev = ["pytest>=8,<9", "pytest-qt>=4.4,<5", "pyinstaller>=6,<7"]

[project.scripts]
turntable-control = "turntable_control.main:main"

[build-system]
requires = ["setuptools>=75"]
build-backend = "setuptools.build_meta"

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
```

- [ ] **Step 3: 先写失败的冒烟测试**

```python
def test_package_exposes_version():
    import turntable_control
    assert turntable_control.__version__ == "0.1.0"
```

Run: `python -m pytest pc/tests/test_smoke.py -q`

Expected: FAIL，模块或 `__version__` 尚不存在。

- [ ] **Step 4: 添加最小包实现并运行测试**

```python
__version__ = "0.1.0"
```

Run: `python -m pytest pc/tests/test_smoke.py -q`

Expected: `1 passed`。

- [ ] **Step 5: 创建工作日志模板并提交**

日志固定包含：时间、目标、完成项、验证证据、风险/限制、下一步和额度检查状态。

Run: `git add .gitignore README.md pc scripts docs/worklog && git commit -m "chore: initialize turntable control workspace"`

Expected: 首次提交成功。

---

### Task 2: 固化寄存器协议与纯运动规则

**Files:**
- Create: `plc/register-map.md`
- Create: `pc/src/turntable_control/registers.py`
- Create: `pc/src/turntable_control/domain.py`
- Create: `pc/tests/test_registers.py`
- Create: `pc/tests/test_domain.py`

**Interfaces:**
- Produces: `Register`, `Mode`, `Direction`, `RunState`, `RunStatus`, `encode_i32()`, `decode_i32()`, `manual_target()`, `automatic_target()`。
- Consumes: 无。

- [ ] **Step 1: 写寄存器编解码失败测试**

```python
@pytest.mark.parametrize("value", [-360000, -1, 0, 1, 360000, 0x12345678])
def test_i32_round_trip(value):
    assert decode_i32(encode_i32(value)) == value
```

Run: `python -m pytest pc/tests/test_registers.py -q`

Expected: FAIL，函数尚未定义。

- [ ] **Step 2: 实现明确的高字在前 32 位格式**

```python
def encode_i32(value: int) -> tuple[int, int]:
    raw = value & 0xFFFFFFFF
    return (raw >> 16) & 0xFFFF, raw & 0xFFFF

def decode_i32(words: tuple[int, int] | list[int]) -> int:
    raw = ((words[0] & 0xFFFF) << 16) | (words[1] & 0xFFFF)
    return raw - 0x1_0000_0000 if raw & 0x8000_0000 else raw
```

- [ ] **Step 3: 写运动边界失败测试**

```python
def test_auto_rejects_target_past_positive_limit():
    with pytest.raises(MotionRejected, match="方向空间不足"):
        automatic_target(100.0, Direction.CW)

def test_manual_clamps_to_one_turn_and_global_limit():
    assert manual_target(100.0, Direction.CW) == 360.0
    assert manual_target(100.0, Direction.CCW) == -260.0
```

- [ ] **Step 4: 实现纯运动规则**

```python
SOFT_MIN_DEG = -360.0
SOFT_MAX_DEG = 360.0

def automatic_target(position_deg: float, direction: Direction) -> float:
    target = position_deg + 360.0 * direction.value
    if not SOFT_MIN_DEG <= target <= SOFT_MAX_DEG:
        raise MotionRejected("该方向空间不足，请反向运行")
    return target

def manual_target(position_deg: float, direction: Direction) -> float:
    target = position_deg + 360.0 * direction.value
    return max(SOFT_MIN_DEG, min(SOFT_MAX_DEG, target))
```

- [ ] **Step 5: 形成唯一寄存器表**

命令区使用 `D1000-D1099`，状态区使用 `D1100-D1199`，时间同步采样区使用 `D1200-D1299`，事件缓冲区从 `D2000` 开始。事件固定占 6 个寄存器：序号、行程角度、实际位置高字/低字、PLC 毫秒高字/低字；一次最多 360 条，末地址为 `D4159`。所有多字整数均高字在前。

- [ ] **Step 6: 运行并提交**

Run: `python -m pytest pc/tests/test_registers.py pc/tests/test_domain.py -q`

Expected: 全部通过。

Run: `git add plc/register-map.md pc/src pc/tests && git commit -m "feat: define motion rules and modbus contract"`

---

### Task 3: 实现逐度事件算法和 PLC 行为模拟器

**Files:**
- Create: `pc/src/turntable_control/simulator.py`
- Create: `pc/tests/test_degree_events.py`
- Create: `pc/tests/test_simulator_motion.py`

**Interfaces:**
- Produces: `crossed_degree_events(previous, current, origin, direction) -> list[int]` 与 `TurntableSimulator.tick(delta_ms)`。
- Consumes: Task 2 的枚举、运动规则和寄存器协议。

- [ ] **Step 1: 写跨多个整数角度的失败测试**

```python
def test_cw_crossing_never_drops_intermediate_degrees():
    assert crossed_degree_events(0.2, 3.4, 0.0, Direction.CW) == [1, 2, 3]

def test_ccw_uses_positive_travel_angles():
    assert crossed_degree_events(10.0, 7.8, 10.0, Direction.CCW) == [1, 2]
```

- [ ] **Step 2: 实现跨越检测**

```python
def crossed_degree_events(previous, current, origin, direction):
    prev_travel = (previous - origin) * direction.value
    curr_travel = (current - origin) * direction.value
    first = max(1, math.floor(prev_travel) + 1)
    last = min(360, math.floor(curr_travel))
    return list(range(first, last + 1)) if last >= first else []
```

- [ ] **Step 3: 写自动一圈、停止和心跳失败测试**

```python
def test_auto_10_deg_per_second_finishes_with_360_events():
    sim = TurntableSimulator(position_deg=0.0)
    sim.start(Mode.AUTO, Direction.CW, speed_deg_s=10.0)
    sim.run_until_stopped(step_ms=10)
    assert sim.position_deg == pytest.approx(360.0, abs=0.001)
    assert [e.travel_angle_deg for e in sim.events] == list(range(1, 361))

def test_heartbeat_timeout_aborts_motion():
    sim = TurntableSimulator()
    sim.start(Mode.AUTO, Direction.CW, speed_deg_s=1.0)
    sim.tick(1001, heartbeat_updated=False)
    assert sim.run_status is RunStatus.COMMUNICATION_ABORTED
```

- [ ] **Step 4: 实现梯形速度曲线模拟、软限位和事件缓存**

模拟器使用与规格相同的加速度、减速度和停止减速度，事件时间使用整数毫秒，缓存满 360 条后拒绝覆盖。

- [ ] **Step 5: 运行并提交**

Run: `python -m pytest pc/tests/test_degree_events.py pc/tests/test_simulator_motion.py -q`

Expected: 全部通过，10°/s 自动一圈生成恰好 360 条事件。

Run: `git add pc/src/turntable_control/simulator.py pc/tests && git commit -m "feat: add deterministic turntable simulator"`

---

### Task 4: 编写 AutoShop PLC 参考程序

**Files:**
- Create: `plc/src/Turntable_Constants.st`
- Create: `plc/src/Turntable_RegisterCodec.st`
- Create: `plc/src/FB_DegreeLogger.st`
- Create: `plc/src/FB_TurntableControl.st`
- Create: `plc/src/PRG_MAIN.st`
- Create: `plc/README.md`
- Create: `pc/tests/test_plc_source_contract.py`

**Interfaces:**
- Produces: AutoShop 可粘贴的 LiteST 程序和一页一页的工程组态步骤。
- Consumes: Task 2 寄存器表和 Task 3 事件算法。

- [ ] **Step 1: 写 PLC 源码契约失败测试**

```python
def test_plc_source_uses_required_motion_instructions():
    source = Path("plc/src/PRG_MAIN.st").read_text(encoding="utf-8")
    for name in ["MC_Power", "MC_SetPosition", "MC_MoveRelative", "MC_Stop",
                 "MC_ReadActualPosition", "MC_ReadActualVelocity"]:
        assert name in source
```

- [ ] **Step 2: 编写状态机和 PLCopen 调用**

`PRG_MAIN.st` 必须采用上升沿命令，示例调用固定为：

```iecst
MC_MoveRelative(
    Execute := bMoveExecute,
    Axis := Axis_0,
    Distance := fMoveDistance,
    Velocity := fSelectedVelocity,
    Acceleration := fAcceleration,
    Deceleration := fDeceleration,
    CurveType := 0,
    Done => bMoveDone,
    Busy => bMoveBusy,
    CommandAborted => bMoveAborted,
    Error => bMoveError,
    ErrorID => wMoveErrorID);

MC_Stop(
    Execute := bStopExecute,
    Axis := Axis_0,
    Deceleration := fStopDeceleration,
    CurveType := 0,
    Done => bStopDone,
    Busy => bStopBusy,
    Error => bStopError,
    ErrorID => wStopErrorID);
```

`MC_Stop.Execute` 在 `Done=TRUE` 后必须复位，否则轴停留在 Stopping 状态。

- [ ] **Step 3: 实现逐度缓存和不覆盖握手**

`FB_DegreeLogger` 输入上一位置、当前位置、运行起点、方向和毫秒计数；每跨过一个整数行程角度写入 `D2000 + index*6`，更新事件数量，并在电脑确认保存前保持 `buffer_ready=TRUE`。

- [ ] **Step 4: 编写 AutoShop 组态说明**

说明必须逐项覆盖：Easy521-0808TN 新建工程、扫描 SV660N、添加 Axis_0、自动映射 6040h/607Ah/6081h/6083h/6084h/6060h 与 6041h/6064h/6061h、线性坐标、用户单位、50:1 传动换算、软件限位、1 ms EtherCAT 周期、1 ms 恒定主扫描周期、MAIN 调用、编译下载、退出在线调试模式后再执行 PLC 指令。

PLC 使用一个 32 位无符号扫描计数 `udiPlcTickMs` 作为运行中单调毫秒基准，每个 1 ms 恒定主扫描周期递增一次；事件保存 `udiPlcTickMs - udiRunStartTickMs`。该计数不要求断电保持，49.7 天回绕由无符号减法和电脑端编解码处理。实际主扫描超时或周期抖动必须在验证报告中记录，因此 1 ms 仍只代表记录分辨率。

- [ ] **Step 5: 运行静态契约测试并提交**

Run: `python -m pytest pc/tests/test_plc_source_contract.py -q`

Expected: required motion instructions、寄存器常量和 360 条缓存声明全部存在。

Run: `git add plc pc/tests/test_plc_source_contract.py && git commit -m "feat: add Easy521 PLC reference program"`

---

### Task 5: 实现 Modbus TCP 客户端与安全命令序号

**Files:**
- Create: `pc/src/turntable_control/modbus_client.py`
- Create: `pc/tests/test_modbus_client.py`

**Interfaces:**
- Produces: `TurntableModbusClient.connect()`, `read_status()`, `send_start()`, `send_stop()`, `set_zero()`, `reset_alarm()`, `write_heartbeat()`。
- Consumes: Task 2 的寄存器和数据类。

- [ ] **Step 1: 写分块读取和错误处理失败测试**

```python
def test_read_events_never_requests_over_125_registers(fake_transport):
    client = TurntableModbusClient(transport=fake_transport)
    client.read_events(360)
    assert max(call.count for call in fake_transport.read_calls) <= 120

def test_reconnect_does_not_reissue_start(fake_transport):
    client = TurntableModbusClient(transport=fake_transport)
    client.send_start()
    client.reconnect()
    assert fake_transport.writes_to(Register.START_SEQ) == 1
```

- [ ] **Step 2: 封装 pymodbus 3.14 同步 API**

```python
response = self._client.read_holding_registers(
    address=address, count=count, device_id=self.device_id
)
if response.isError():
    raise CommunicationError(str(response))
```

- [ ] **Step 3: 命令采用递增序号**

启动、停止、设零和复位分别维护 16 位序号；重连只读取 PLC 已确认序号，不自动重放。停止命令绕过普通设置队列，优先写入 STOP_SEQ。

- [ ] **Step 4: 运行并提交**

Run: `python -m pytest pc/tests/test_modbus_client.py -q`

Expected: 全部通过。

Run: `git add pc/src/turntable_control/modbus_client.py pc/tests && git commit -m "feat: add safe modbus client"`

---

### Task 6: 实现 PLC—Windows 时间换算与 CSV 持久化

**Files:**
- Create: `pc/src/turntable_control/time_sync.py`
- Create: `pc/src/turntable_control/csv_store.py`
- Create: `pc/tests/test_time_sync.py`
- Create: `pc/tests/test_csv_store.py`

**Interfaces:**
- Produces: `ClockSynchronizer.add_sample()`, `to_epoch_ms()`, `CsvStore.save_run()`。
- Consumes: PLC 事件的 `plc_elapsed_ms` 与运行元数据。

- [ ] **Step 1: 写最低往返延迟样本测试**

```python
def test_clock_sync_prefers_lowest_round_trip_sample():
    sync = ClockSynchronizer()
    sync.add_sample(pc_send_ms=1000, plc_ms=500, pc_recv_ms=1040)
    sync.add_sample(pc_send_ms=2000, plc_ms=1498, pc_recv_ms=2004)
    assert sync.to_epoch_ms(1600) == 2104
```

- [ ] **Step 2: 实现中点偏移估计**

```python
midpoint = (pc_send_ms + pc_recv_ms) / 2.0
offset_ms = midpoint - plc_ms
sample = ClockSample(round_trip_ms=pc_recv_ms-pc_send_ms, offset_ms=offset_ms)
self._samples = sorted([*self._samples, sample], key=lambda s: s.round_trip_ms)[:8]
```

- [ ] **Step 3: 写 CSV 原子保存失败测试**

```python
def test_failed_replace_keeps_temporary_and_does_not_ack(tmp_path, monkeypatch):
    store = CsvStore(tmp_path)
    monkeypatch.setattr(os, "replace", Mock(side_effect=OSError("disk full")))
    with pytest.raises(CsvSaveError):
        store.save_run(sample_run())
    assert list(tmp_path.glob("*.tmp"))
```

- [ ] **Step 4: 实现 UTF-8 BOM CSV 和原子改名**

先写 `<test_id>.csv.tmp`，刷新并关闭后使用 `os.replace` 改名为 `<test_id>.csv`；只有改名成功才允许 Controller 向 PLC 发送缓冲区确认。

- [ ] **Step 5: 运行并提交**

Run: `python -m pytest pc/tests/test_time_sync.py pc/tests/test_csv_store.py -q`

Expected: 全部通过。

Run: `git add pc/src/turntable_control/time_sync.py pc/src/turntable_control/csv_store.py pc/tests && git commit -m "feat: add timestamp conversion and durable csv logging"`

---

### Task 7: 实现控制器、心跳和测试会话

**Files:**
- Create: `pc/src/turntable_control/controller.py`
- Create: `pc/tests/test_controller.py`

**Interfaces:**
- Produces: Qt 无关的 `TurntableController`，通过回调发布连接、状态、错误和运行完成事件。
- Consumes: Tasks 2、5、6。

- [ ] **Step 1: 写启动前联锁失败测试**

```python
@pytest.mark.parametrize("zero_valid,servo_ready,expected", [
    (False, True, "未设零"),
    (True, False, "伺服未就绪"),
])
def test_start_interlocks(zero_valid, servo_ready, expected):
    ctl = controller_with_status(zero_valid=zero_valid, servo_ready=servo_ready)
    with pytest.raises(CommandRejected, match=expected):
        ctl.start(Mode.AUTO, Direction.CW, 1.0)
```

- [ ] **Step 2: 实现 100 ms 轮询和 250 ms 心跳**

后台线程只执行 Modbus I/O；UI 线程只接收不可变状态快照。通信异常连续发生后立即发布断线，不缓存启动命令。

- [ ] **Step 3: 实现运行完成下载流程**

状态变为完成或中止时：读取事件数量→分块下载→验证序号连续→时间换算→原子保存 CSV→发送缓冲确认。任何一步失败都保留 PLC 缓冲并在界面显示可重试错误。

- [ ] **Step 4: 运行并提交**

Run: `python -m pytest pc/tests/test_controller.py -q`

Expected: 联锁、心跳、断线、不重发和 CSV 确认测试全部通过。

Run: `git add pc/src/turntable_control/controller.py pc/tests && git commit -m "feat: coordinate safe turntable sessions"`

---

### Task 8: 实现 PySide6 Windows 控制界面

**Files:**
- Create: `pc/src/turntable_control/ui.py`
- Create: `pc/src/turntable_control/main.py`
- Create: `pc/tests/test_ui.py`

**Interfaces:**
- Produces: 桌面主窗口和 `turntable-control` 命令。
- Consumes: Task 7 Controller。

- [ ] **Step 1: 写关键控件和运行锁定失败测试**

```python
def test_stop_button_is_always_enabled(qtbot, window):
    window.apply_status(running_status())
    assert window.stop_button.isEnabled()

def test_running_disables_mode_direction_and_speed(qtbot, window):
    window.apply_status(running_status())
    assert not window.mode_combo.isEnabled()
    assert not window.direction_combo.isEnabled()
    assert not window.speed_combo.isEnabled()
```

- [ ] **Step 2: 构建五区界面**

界面包含连接、角度状态、运行设置、操作按钮和折叠调试参数；红色按钮文本固定为“停止（软件）”，工具提示固定说明“不能替代实体急停”。

- [ ] **Step 3: 添加停止快捷键与明确确认**

空格键在主窗口激活时触发软件停止；设零和修改减速比必须弹出确认框；运行中不允许弹出阻塞停止按钮的模态窗口。

- [ ] **Step 4: 运行无头 UI 测试并提交**

Run: `$env:QT_QPA_PLATFORM='offscreen'; python -m pytest pc/tests/test_ui.py -q`

Expected: 全部通过。

Run: `git add pc/src/turntable_control/ui.py pc/src/turntable_control/main.py pc/tests && git commit -m "feat: add Windows turntable control interface"`

---

### Task 9: 完成模拟端到端测试、脚本和安装包

**Files:**
- Create: `pc/tests/test_end_to_end.py`
- Create: `scripts/run-app.ps1`
- Create: `scripts/run-simulator.ps1`
- Create: `docs/verification-report.md`
- Modify: `README.md`

**Interfaces:**
- Produces: 无 PLC 时可运行的完整演示、Windows 启动脚本和可执行文件。
- Consumes: Tasks 1-8。

- [ ] **Step 1: 写 10°/s 自动一圈端到端测试**

```python
def test_auto_run_exports_360_timestamped_rows(tmp_path):
    system = simulated_system(data_dir=tmp_path)
    system.set_zero()
    system.start(Mode.AUTO, Direction.CW, 10.0)
    system.run_until_idle()
    rows = list(csv.DictReader(next(tmp_path.glob("*.csv")).open(encoding="utf-8-sig")))
    assert [int(r["travel_angle_deg"]) for r in rows] == list(range(1, 361))
    assert all(r["run_status"] == "COMPLETED" for r in rows)
```

- [ ] **Step 2: 覆盖正反转、五档速度、中止、断线和边界**

参数化执行十种自动方向/速度组合，并验证预计时间、事件连续、最终位置和状态；验证 +360°继续正转和 -360°继续反转均被拒绝。

- [ ] **Step 3: 生成 Windows 可执行文件**

Run: `python -m PyInstaller --noconfirm --windowed --name TurntableControl --paths pc/src pc/src/turntable_control/main.py`

Expected: `dist/TurntableControl/TurntableControl.exe` 存在并可启动。

- [ ] **Step 4: 完整验证并提交证据**

Run: `python -m pytest pc/tests -q`

Expected: 全套测试通过，无跳过项。

Run: `git add README.md scripts pc/tests docs/verification-report.md && git commit -m "test: verify complete simulated control workflow"`

---

### Task 10: 实际 PLC 网络与安全联机验证

**Files:**
- Modify: `docs/worklog/2026-08-11-development-log.md`
- Modify: `docs/verification-report.md`
- Modify: `plc/README.md`

**Interfaces:**
- Produces: 实际网络、Modbus 502 和只读寄存器验证结果；运动验证只在明确满足空载低速条件时执行。
- Consumes: 所有前序任务。

- [ ] **Step 1: 只读检查电脑网卡、PLC IP 和 502 端口**

列出电脑 Ethernet 适配器、IPv4 和 ARP；确认电脑与 PLC 同网段后执行 ping 和 TCP 502 连接测试。禁止扫描无关网络接口和无关地址段。

- [ ] **Step 2: 验证 Modbus 字序魔数**

PLC 程序向状态寄存器写入 `0x12345678`，电脑读取并确认高字/低字顺序；若实际顺序相反，只修改 `registers.py` 的统一编解码器和寄存器文档，随后重新运行全部协议测试。

- [ ] **Step 3: 下载前编译门禁**

在 AutoShop 中编译必须为 0 error；确认已退出在线调试模式，轴状态为 Standstill，平台空载，初始速度为 1°/s，人员远离旋转范围。

- [ ] **Step 4: 只执行低风险动作验收**

按顺序执行：使能→人工设零→正向 1°→停止→反向 1°→停止→电脑断线停止。任一步方向错误、异响、报警或位置异常立即停止，不继续一圈测试。

- [ ] **Step 5: 记录实际限制与提交**

无法由当前环境自动操作的 AutoShop GUI、实体急停缺失和需要现场观察的机械方向必须明确列为“现场待验证”，不能写成已通过。

Run: `git add docs plc/README.md && git commit -m "docs: record PLC commissioning evidence"`

---

## Plan Self-Review Checklist

- [ ] 规格中的机械换算、五档速度、两种模式、双向、人工设零、±360°、平滑加减速、软件停止、断线停止、逐度缓存、时间戳和 CSV 均有对应任务。
- [ ] 电脑重连不重发启动，CSV 保存失败不清 PLC 缓冲，跨多个整数角度不漏点。
- [ ] 1 ms 明确为字段分辨率，实际绝对时间精度写入验证报告。
- [ ] 无实体急停时不执行高于 1°/s 的现场运动验证。
- [ ] 计划不宣称 AutoShop 编译、实际方向或机械角度已经验证。
