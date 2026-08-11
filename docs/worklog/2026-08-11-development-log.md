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

## Task 4: Easy521 AutoShop PLC reference source

### Completed work

- Added a five-file AutoShop LiteST reference source set: fixed Modbus/register and motion constants, explicit high-word-first signed-DINT codecs, a 360-record fixed event logger, deterministic command/state control, and `PRG_MAIN` PLCopen call ordering.
- Added the page-by-page `plc/README.md` guide with the full `D1000..D1206` and `D2000..D4159` INT variable-table binding, EtherNET1/CN3 separation, PDO checks, axis-unit/ratio setup, commissioning limits, and bounded timing/resource constraints.
- Added source-contract tests first. The initial test run failed as expected because the referenced PLC sources and README did not exist; after implementation, the static contract passes.

### Constraints and limitations

- This is reference source only. AutoShop's installed compiler and firmware-specific type names remain the final authority; no AutoShop compilation is claimed.
- No network, PLC, servo, download, online operation, or hardware motion was performed.
- The communication heartbeat and controlled `MC_Stop` are not a physical emergency stop or a safety-rated function.

## Task 4 review remediation: fail-safe control flow and u32 tick contract

### Completed work

- Reworked PLC reference control flow so feedback/motion faults while active latch a controlled Stop request and retain the run until `MC_Stop.Done`; `MC_Stop.Error` now publishes an unsafe terminal fault, inhibits the reference power-enable output, blocks restart, and preserves unsealed data for on-site assessment.
- Added a latched accepted target, stop/start race consumption, real `MC_Reset` Done/Error acknowledgement flow, deterministic rejection acknowledgement for every start sequence, stale-heartbeat start rejection, and accepted-run-only acceleration/deceleration latching.
- Replaced the 60-crossing logger cap with a fixed 360-crossing pass, added raw-u32 tick/event codecs in PLC and PC code, and made the Modbus D-word table globally owned rather than duplicated in `PRG_MAIN`.

### Test-first evidence and limitations

- The reviewer regressions first failed with 20 focused failures (missing u32 codec and required source structures); after remediation, focused source/register tests and the complete PC suite pass.
- AutoShop compiler, firmware-specific LiteST names, axis scaling, and the 360-write worst-case scan time remain field validation work. No AutoShop compile, PLC, network, servo, download, online operation, or hardware motion was performed.

## Task 5: Safe Modbus TCP client

### Completed work

- Added a lock-protected synchronous pymodbus 3.14 adapter with an injectable deterministic fake transport for all tests.
- Added read-only protocol and word-order probing before writes, typed status/event decoding, 120-word event chunks, and per-command u16 sequence tracking with reconnects that never replay commands.
- Added fixed phase-1 start parameters, direct software-stop writes, heartbeat validation, buffer acknowledgement and time-sync sequence support. The software stop is not an emergency stop.

### Test-first evidence and constraints

- The new focused test suite was run before the module existed and produced the expected 16 failures, all due to `ModuleNotFoundError: turntable_control.modbus_client`.
- After implementation, the focused suite and complete PC suite pass using only the in-memory fake transport. No real network, PLC, servo, or other hardware access was performed.

## Task 5 review remediation: invalidate uncertain Modbus sessions

### Completed work

- Any write error or exception now invalidates and closes the session before the communication error is returned. This prevents stale command words from being reused after an ambiguous PLC-side write.
- Read I/O failures, malformed/short reads, and protocol-probe drift during status polling now also invalidate the verified session; a reconnect must read fresh command/ack state before further commands.
- Event records now require the exact sequence prefix `record_index + 1`. Fake-transport coverage includes unexecuted write exceptions, write-then-error ambiguity, parameter interruption before START, protocol drift, and reconnect behavior.

### Constraint

- An ambiguous START response must be reconciled by reconnecting and checking PLC state. It must never be blindly retried. No real network, PLC, servo, or hardware access was performed.

## Task 6: PLC time conversion and durable CSV export

### Completed work

- Added bounded lowest-RTT clock sampling with midpoint offsets, deterministic newest-tie selection, raw-u32 wrap handling, explicit half-range rejection, and run-start-plus-elapsed conversion.
- Added validated BOM-prefixed CSV export with UTC and China (`+08:00`) timestamps, fixed decimal formatting, root containment, non-overwrite protection, and flush/fsync/atomic-replace durability handling.
- Added focused tests for timing, input validation, event ordering, CSV content, atomic failure preservation, and no communication/acknowledgement dependency.

### Test-first evidence and constraints

- The focused tests were run before either production module existed and failed at collection only because `turntable_control.time_sync` and `turntable_control.csv_store` were absent.
- No real network, PLC, hardware, buffer acknowledgement, or client communication was accessed or performed.

## Task 6 review remediation: evidence claim locking and exact timestamps

### Completed work

- Added per-test-id exclusive lock claims held from the final-file check through temporary write, fsync, and atomic replace. The process that acquired the lock always closes and removes it; a crash-surviving lock requires explicit operator inspection and manual recovery.
- Made CSV run metadata require the exact domain `Mode`, `Direction`, and `RunStatus` enum instances and export their safe enum names rather than arbitrary text.
- Replaced floating-point clock midpoint/offset arithmetic with exact `Fraction` values and exact half-away-from-zero rounding for very large positive and negative values.

### Test-first evidence and constraints

- Reviewer regression tests first failed against the prior implementation: concurrent CSV saves had no exclusive claim, arbitrary string metadata was accepted, and midpoint/rounding lost precision at values larger than `2**53`.
- Added two interleaved thread tests, final-plus-stale-lock behavior, strict metadata validation, and exact large-value rounding coverage. No real network, PLC, hardware, buffer acknowledgement, or client communication was accessed or performed.

## Task 6 review remediation: lock cleanup failure reporting

### Completed work

- Lock close/remove failures no longer leak native filesystem exceptions or replace the CSV save error that caused cleanup to run.
- If `os.replace` has already safely published the final CSV, a cleanup failure now emits a `RuntimeWarning` with manual lock-inspection/removal guidance and still returns the final path for the later controller.
- If publication failed first, cleanup failure emits a warning while preserving the original `CsvSaveError`; the temporary CSV and lock remain as diagnostic evidence.

### Test-first evidence and constraints

- Added close/unlink failure regressions for both pre-publication and post-publication paths. They first demonstrated that direct `finally` cleanup raised raw `OSError` and masked the intended outcome.
- No real network, PLC, hardware, buffer acknowledgement, or client communication was accessed or performed.

## Task 7: Safe controller sessions, heartbeat, and exact run-start tick

### Completed work

- Added the immutable, Qt-independent `TurntableController` snapshot/callback API and a bounded command queue whose public methods perform no Modbus or CSV I/O.
- Serialized all client and CSV operations through one deterministic pump/background worker; STOP has a dedicated priority slot that removes pending START, while disconnects and communication failures clear unsafe queued commands without replay.
- Added fresh status interlocks immediately before START, fixed speed-to-index mapping, 100 ms polling, 250 ms raw-u16 heartbeat scheduling, matching-sequence clock samples, and exact PLC-published run-start tick capture.
- Added terminal event handling in the strict order read prefix → durable CSV save → buffer acknowledgement. Failed reads/saves retain the buffer for explicit retry; an uncertain ACK retains the durable path and reuses it after reconnect without another save or motion retry.
- Extended protocol version 1 with high-word-first raw-u32 `D1119:D1120 RUN_START_TICK_MS`, synchronizing the Python register contract, Modbus status decoder, PLC constants/global table, `PRG_MAIN` publication, and AutoShop reference documentation.

### Test-first evidence and constraints

- Protocol tests first produced five expected failures for the absent D1119:D1120 addresses, status decode, and PLC publication path; the focused protocol suite passed after the minimal synchronized change.
- Controller tests first failed at collection with `ModuleNotFoundError: turntable_control.controller`. Subsequent RED groups demonstrated missing worker-side interlock/error handling, scheduling/sync, terminal download/ACK recovery, callback reporting, pump shutdown, stale sync invalidation, and generation-wrap handling before each minimal implementation.
- All controller tests use injected fake clients/stores and temporary paths. No real network, PLC, AutoShop, servo, or hardware operation was accessed or performed.
- AutoShop compilation and actual PLC timing/word order remain controlled on-site verification work; the software stop and communication heartbeat are not a physical emergency stop.

## Task 7 review remediation: session reconciliation and failure containment

### Completed work

- Added a single queued/in-flight START guard and explicit `StartNotIssued` versus `StartOutcomeUnknown` client outcomes. STOP, disconnect, and definite pre-START rejection release the normal guard; an uncertain START never creates a wildcard run session. On reconnect an exact sequence plus consistent running/terminal PLC evidence rebuilds only the local session without replaying motion, a definite READY/IDLE rejection clears the uncertainty, and unknown/conflicting evidence remains blocked for manual reconciliation.
- Reconciliation also validates the PLC state/buffer pair: running evidence must match the requested mode with no retained buffer, non-fault terminal evidence must be READY with a buffer, and fault terminal evidence must be FAULT with a buffer. Because the PLC echoes rejected START sequences too, READY/IDLE/no-buffer clears uncertainty regardless of whether the acknowledgement equals the attempted sequence.
- Once a matching running/terminal status captures the PLC-published run-start tick, later status reads must retain that exact tick. A changed tick or a terminal buffer paired with a contradictory run state is treated as a session-fingerprint conflict and is never saved or ACKed.
- Replaced generation-only ACK recovery with a complete durable evidence record covering the exact session token, START acknowledgement, generation, event count, terminal status, run-start tick, test ID, and published CSV path. Reconnect refuses ACK when PLC or published evidence differs and reports a manual reconciliation error.
- Required every event travel angle to equal its sequence and required AUTO/COMPLETED runs to contain all 360 events before CSV publication or buffer ACK.
- Made shutdown always publish disconnected, surface worker/close failures as `ControllerStopped`, consume invalid matching time-sync responses without killing polling/heartbeat, and publish/process the fresh status used for the final START interlock.
- Fixed the background single-thread test so its event wait cannot pass from earlier connect I/O and it proves the queued command actually ran.
- Rejected switching from an already claimed deterministic pump to a new background worker, preventing the background finalizer from closing the client on a second I/O thread.

### Test-first evidence and constraints

- Review regressions reproduced unsafe ACK of mismatched evidence, ambiguous START ownership, incomplete completed runs, stale fresh-status publication, an escaping backward-clock sample, and both deterministic/background close failures before their corresponding fixes. The lifecycle group initially reported five failures plus one passing non-vacuous worker test; the added published-evidence pair also failed by attempting a second ACK before the full local fingerprint check.
- Fresh final verification passed 196 focused controller/protocol/storage/time tests and all 251 PC tests; source compilation and Git whitespace checks also passed. An independent read-only code review reported no remaining Critical, Important, or Minor findings.
- All regression and verification runs used only fake transports, fake clients/stores, injected clocks, and temporary local files. No PLC, network endpoint, AutoShop session, servo, or hardware was accessed or written.

## Task 7 final-review remediation: serialized safety boundaries

### Completed work

- Exposed the verified command-side `D1003 START_SEQ` from each Modbus session and reconciled uncertain START outcomes against both D1003 and `START_ACK_SEQ`. A still-pending D1003 value remains blocked; only proof that the sequence was not written or the PLC's explicit `FAULT_START_REJECTED` contract clears it, while coherent exact running/terminal evidence rebuilds the local session without replay.
- Centralized terminal coherence across uncertain reconciliation, normal save, and durable ACK recovery. The durable fingerprint now includes `run_state`; terminal state must be READY except FAULTED/FAULT, and MANUAL_STOPPED/AUTOMATIC_ABORTED must match the session mode.
- Added a lock-linearized final START check after fresh-status callbacks. Shutdown, STOP, disconnect, or replacement/cancellation of the exact pending command wins before START I/O; the lock remains held through the write boundary so later priority requests order after an already-issued command.
- Added an atomic deterministic/background I/O mode reservation. Background ownership is reserved before thread start, external pump calls are rejected without killing the worker, and finalization never closes the client from a thread that failed ownership.
- Split queued, issued, confirmed-active, and cancelling START state. STOP immediately removes a queued START but retains a stop-only barrier until STOP ACK plus a coherent idle/retained-terminal state. A pre-ACK issued START additionally requires its exact START ACK; a confirmed active run can clear only from exact acknowledgements plus a mode-compatible terminal buffer. Repeated STOP requests retain or replace the barrier atomically so an older ACK cannot expose a replacement START.

### Test-first evidence and constraints

- R1 RED cases exposed the missing client command-side sequence and premature clearing for D1003-pending and exact-ACK/READY-IDLE snapshots. R2 RED cases saved impossible mode/status pairs and ACKed a recovery snapshot with mismatched run state. R3 callback and barrier tests reproduced START after STOP/disconnect. R4 reproduced caller-side pump ownership after background reservation. R5 reproduced replacement START before both acknowledgements and stopped state.
- Fresh final verification passed 220 focused controller/protocol/storage/time tests and all 275 PC tests; source compilation and Git whitespace checks passed. The independent read-only final review reran 168 focused tests and all 275 PC tests and reported no remaining Critical, Important, or Minor findings.
- All tests use in-memory fakes, injected clocks/barriers, and temporary local files. No PLC, network endpoint, AutoShop session, servo, or hardware was accessed or written.

## Task 7 final closure: explicit session finalization

### Completed work

- Kept a confirmed run session active after its issued START acknowledgement, so a contradictory exact-ACK READY/IDLE/no-buffer snapshot is reported as missing terminal evidence and cannot authorize a replacement START.
- Added an explicit session-sealing transition after a durable CSV buffer acknowledgement succeeds, or after reconnect proves an uncertain acknowledgement already cleared the PLC buffer. Sealing clears the historical active session and reduces any still-pending STOP to a stop-only acknowledgement barrier.
- Serialized uncertain-START reconciliation under the controller lock. Definite non-write, explicit rejection, and coherent reconstruction now commit uncertainty/session/pending-command state atomically, so a concurrent public START cannot return successfully and then be silently discarded.

### Test-first evidence and constraints

- RED regressions reproduced replacement START after confirmed RUNNING evidence disappeared, a permanent STOP barrier after normal save/ACK, the same lifecycle wedge after an uncertain ACK whose buffer was already cleared, and a barrier-controlled concurrent START being erased by uncertain reconciliation.
- The independent latest-tree review reported SF1, SF2, and QF1 closed with no remaining Critical, Important, or Minor findings; its fresh runs passed 172 focused tests and all 279 PC tests.
- Fresh final verification passed 224 focused controller/protocol/storage/time tests and all 279 PC tests; source compilation and Git whitespace checks passed. All tests remained fake/local with injected clocks/events and temporary files; no network, PLC, AutoShop session, servo, or hardware was accessed or written.

## Task 8: PySide6 中文 Windows 控制界面

### 完成项

- 新增五区中文操作界面、固定枚举/速度映射、三位小数带符号角度与速度显示、运行/终止/中止状态映射、逐度事件数量及 CSV 保存/重试区域。
- 软件停止按钮固定为红色“停止（软件）”，明确提示“不能替代实体急停”，在断线、未知状态、运行和停止过程中始终可用；主窗口空格键走相同控制器停止入口。
- `ControllerBridge` 将控制器工作线程的快照、错误和保存回调经 Qt 信号送回 GUI 线程，界面只调用 `TurntableController`，不直接调用 Modbus。
- 首次有效 IPv4 连接才创建生产控制器并启动后台工作线程；同 IP 重连复用控制器且不重发启动，断线后更换 IP 会先取消订阅并在 2 秒有界超时内关闭旧控制器。
- 精确 READY/零点有效/伺服就绪/无缓冲/无待下载/无本地命令错误时才允许启动。已知运动状态、停止状态和本地启动待确认均锁定设置；未知原始状态按未知显示并禁止启动。
- 设零仅在非运行状态显示中文人工目视对零确认，所有命令错误均使用界面内提示，不在运行/停止过程中弹出阻塞错误对话框。
- 高级参数第一阶段全部只读并默认收起。该行为按 Task 8 安全简报取代旧实施计划中“确认修改减速比”的表述；总减速比/轴缩放必须与 PLC、AutoShop 一致修改，本版本不伪装参数写入成功。
- 桌面入口支持可选 `--plc-ip` 预填和 `--data-dir`，但在操作者点击有效 IPv4 的连接按钮之前不构造 Modbus 客户端，也不会猜测或自动连接 PLC。

### 测试先行证据

- 最初聚焦运行得到预期 `ModuleNotFoundError: No module named 'turntable_control.ui'`，证明模块缺失 RED。
- 五区/停止/高级参数组先得到 4 个构造失败，再完成 4 项 GREEN；状态显示与失效安全组先得到 5 个缺失行为失败，再完成 9 项 GREEN。
- 连接、命令、设零确认和跨线程桥接组先因缺少 `ControllerBridge` 在收集阶段失败，完成后 16 项通过；目录、关闭和入口组先得到 4 个预期失败，完成后通过。
- 自审新增“原始整数即使等于已知枚举值也必须显示未知”和“停止取消本地启动待确认锁”两个回归，先得到 2 个失败，修复后聚焦测试通过。
- 最终聚焦 UI 测试为 `24 passed`，完整 PC 测试为 `303 passed`。所有 UI 测试使用 offscreen Qt、假控制器/工厂、临时目录和假应用。

### 风险、限制与额度检查

- 未访问网络、PLC、AutoShop、伺服或硬件；未验证实际 EtherCAT、方向、机械角度、Windows 字体/缩放、多显示器布局和现场停止距离。
- 软件停止与通信心跳均不是实体急停或安全等级功能；安装并验证实体急停之前仍只允许空载、人员远离和 1°/s 初始调试。
- 未执行额度查询；本任务没有外部服务调用。

## Task 8 自审修正：运行断开与首次连接失败清理

- 运行/停止期间继续锁定 IP、连接、模式、方向、速度、设零、上电、复位和高级参数，但只要控制器快照仍为已连接，“断开”按钮保持可用，使操作者可以主动断开通信并由 PLC 心跳保护执行受控停止；软件停止仍始终可用。
- 首次连接已注册回调且已排队 `connect()` 后，如果后台线程启动失败，界面会立即取消全部三个订阅、调用 2 秒有界 `shutdown()`、丢弃半初始化控制器并恢复可重试状态；清理失败会与原始错误一起显示，不会静默保留失效控制器。
- 本地命令错误不会被下一次无错误状态轮询静默清除并重新开放启动；操作者修改 IP、模式、方向或速度后才清除该本地错误并重新计算联锁。
- 三个回归分别先复现运行中断开被禁用、后台启动失败遗留控制器，以及轮询静默清除本地启动错误；最小修正后对应聚焦测试通过。

## Task 8 独立复审修正：fail-closed 与 Qt 生命周期

### 完成项

- 建立精确安全空闲谓词：只有精确 `RunState.READY` 与精确 `RunStatus.IDLE` 才可能开放启动；连接状态下任何 raw/unknown/missing state 或 status 都锁定除软件 STOP 和 Disconnect 外的全部输入、命令、数据目录和高级参数。
- Disconnect 成功入队以及控制器发布断线快照时都会清除 UI 本地 START 待确认锁，恢复 IP/Connect，不重放 START；不确定启动的真实所有权仍由 Controller 管理。
- CSV 文案改为由 durable path 与 `download_pending` 联合推导，明确区分“未保存”“待下载或保存”“已保存，等待 PLC 确认”和“已保存”；ACK 重试完成后能恢复最终已保存状态。
- 每次安装 ControllerBridge 分配单调世代号；替换、丢弃和关闭前先使旧世代失效，所有 queued snapshot/error/saved Qt 信号只在世代仍为当前且窗口未 closing 时应用。
- ControllerBridge 的三个订阅改为事务化逐项注册，任何后续注册失败都会逆序取消已有订阅。窗口对未安装完成的局部 Controller 执行 2 秒有界 shutdown。
- Controller 替换时在 shutdown 成功前保留原 bridge。shutdown 失败会给同一 bridge 分配新世代并恢复未来回调，绝不留下仍存活但无 bridge 的 Controller；首次后台启动 cleanup 自身失败也采用同一恢复原则。

### 测试先行证据

- SF1-SF3 新回归首先得到 `5 failed`：unknown/raw 控件未锁、READY/RUNNING 错误开放 START、CSV ACK 文案错误、Disconnect 未清本地 pending；最小修复后 5 项通过。
- QF1 使用真实 Python worker 将旧快照排入 Qt 队列，替换/关闭后处理事件，两项首先都被旧快照覆盖并失败；加入世代号与 closing 守卫后 3 项相关线程测试通过。
- QF2 在第 1/2/3 个订阅位置注入失败并注入 replacement shutdown 超时，首先得到 `4 failed`；事务化回滚与 bridge 恢复后 4 项通过。
- 扩展回归先复现首次后台启动与 cleanup 同时失败时 Controller 被丢弃且无 bridge；修复后该 Controller/bridge 保持绑定并能继续接收工作线程错误。
- 当前聚焦 UI 为 `36 passed`，完整 PC 套件为 `315 passed`；全部使用 offscreen Qt、假 Controller/factory、真实本地 Python worker 线程和临时目录。

### 限制

- 未访问网络、PLC、AutoShop、伺服或硬件；queued-callback 验证的是 Qt/Python 本地线程边界，不代表现场 PLC 通信或机械安全验证。
- 软件停止和通信断开触发的心跳保护仍不是实体急停或安全等级功能。
