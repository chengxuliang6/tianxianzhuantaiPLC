# 开发日志（2026-08-11）

## 时间

2026-08-11

## 目标

建立可重复安装的 Python 工程基础、最小包版本检查和开发环境安装入口。

## 已完成工作

- 建立 `turntable-control` 的 setuptools 项目配置及固定运行时依赖。
- 添加开发依赖、控制台入口和统一 pytest 测试路径。
- 采用测试先行方式添加包版本冒烟测试与最小包实现。
- 添加可重复执行的 PowerShell 环境安装脚本、项目说明和忽略规则。

## 验证证据

- 初始测试使用环境中可用的 Python 3.11/pytest 运行，因 `turntable_control` 尚不存在而得到预期的 `ModuleNotFoundError`。
- 已使用 Codex bundled Python 3.12.13 重建仓库根目录 `.venv`，并完成 `-e "pc[dev]"` 正式 editable install。
- 在激活的 Python 3.12.13 环境中运行 `python -m pytest pc/tests/test_smoke.py -q`，结果为 `1 passed`。

## 风险与限制

- 本任务未连接 PLC，未执行任何硬件读写或运动操作。
- 当前控制台入口只输出开发阶段安全提示，不会连接或写入 PLC。

## 下一步

继续实现寄存器协议和纯运动规则。

## Task 2：寄存器协议与纯运动规则

### 已完成工作

- 将 `plc/register-map.md` 固化为唯一 Modbus 寄存器合同：命令/参数为 `D1000-D1099`，状态/运行元数据为 `D1100-D1199`，时间同步和协议探针为 `D1200-D1299`，逐度事件缓冲为 `D2000-D4159`。
- 在电脑端实现高字优先、二进制补码的 32 位寄存器编解码；超出有符号 32 位范围或不是两个字的输入会明确拒绝。
- 定义手动/自动运动、方向、PLC 运行状态和测试状态枚举，以及 ±360° 连续坐标软件限位、固定五档速度和电机转速换算。
- 自动模式严格执行一整圈，越界时返回“该方向空间不足，请反向运行”；手动模式在同一边界内截断。
- 文档明确要求在任何硬件写入前通过 `0x12345678` 魔数验证 PLC 实际字序；本任务未执行 PLC、网络或硬件访问。

### 测试先行证据

- 初始命令：`.\\.venv\\Scripts\\python.exe -m pytest pc/tests/test_registers.py pc/tests/test_domain.py -q`
- 初始结果：预期失败，测试收集阶段报出 `ModuleNotFoundError: No module named 'turntable_control.registers'` 和 `ModuleNotFoundError: No module named 'turntable_control.domain'`，因为实现模块尚未创建。
- 实现后使用同一命令复验：`39 passed`。

### 风险与限制

- 当前字序仅为软件合同；必须在现场读取 `D1201:D1202` 的魔数后，才允许进行 PLC 硬件写入。
- 本任务仅覆盖纯 Python 规则和协议，不包含实际 Modbus 连接或运动控制。

## Task 2 审查修复：强化寄存器合同

### 已完成工作

- 将事件布局元数据 `EVENT_RECORD_WORDS=6` 与 `EVENT_RECORD_COUNT=360` 移出 `Register` 枚举；枚举迭代现在只产生实际分配的 Modbus 地址。
- 将 `decode_i32()` 的输入限定为两个非布尔整数的无符号 16 位字；负数、超过 `65535` 的数和非整数均会得到 `ValueError`，不会再被掩码静默转换。
- 仅从 Git 索引取消跟踪内部 Task 2 报告，保留该忽略目录中的本地报告文件供流程使用。

### 测试先行证据

- 初始命令：`.\\.venv\\Scripts\\python.exe -m pytest pc/tests/test_registers.py -q`
- 初始结果：`9 failed, 14 passed`。失败准确显示：非法字没有抛出 `ValueError`、`Register` 迭代包含 `6`/`360` 元数据、模块级事件布局常量尚不存在。
- 修复后同一命令：`23 passed`。

## 额度检查

未执行额度查询；本任务不涉及用量阈值或外部服务调用。

## Task 3: Deterministic simulator and degree-event buffer

### Completed work

- Added a hardware-free deterministic motion simulator consuming the Task 2 domain contract.
- Added immutable per-degree events, interpolation-based millisecond timestamps, 360-event retention, acknowledgement protection, motion profiles, soft limits, manual/automatic modes, controlled stops, and heartbeat aborts.
- Added focused tests for degree crossings, profiles, soft limits, stops, heartbeat behavior, validation, and repeatability.

### Test-first evidence

- Initial command: `.\\.venv\\Scripts\\python.exe -m pytest pc/tests/test_degree_events.py pc/tests/test_simulator_motion.py -q`
- Initial result: expected collection failure, `ModuleNotFoundError: No module named 'turntable_control.simulator'`.

### Constraints

- No PLC, network, or hardware access was performed.

## Task 3 review follow-up: heartbeat and stop-state hardening

### Root cause and test-first evidence

- Added regressions before changing the simulator. The focused command initially reported `9 failed, 20 passed`: heartbeat age did not advance after a `heartbeat_updated=True` observation, a later stop overwrote a communication abort, and raw integer/boolean enum equivalents crossed the public boundary.
- Defined each heartbeat observation at the start of `tick()`: age resets once, then increments for every simulated millisecond; only an age strictly above 1000 ms starts the communication stop.
- Made the first controlled-stop reason immutable while `STOPPING` and required exact `Mode` and `Direction` enum instances at `start()`.

### Verification

- Focused Task 3 tests: `29 passed in 2.07s`.
- Full PC tests: `78 passed in 2.10s`.
- No PLC, network, or hardware access was performed.
