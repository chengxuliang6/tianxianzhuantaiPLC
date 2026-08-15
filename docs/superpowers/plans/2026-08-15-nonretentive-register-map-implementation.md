# Easy521 非保持寄存器迁移实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将破坏性旧地址协议迁移到 Easy521 的非保持 D 区，升级协议版本并重新交付经过验证的 Windows 程序。

**Architecture:** 命令、状态和对时区分别迁移到 `D0～D19`、`D100～D120`、`D200～D206`，事件数据继续位于 `D2000～D4159`。电脑端以协议版本2、字序魔数和PLC重启检测作为写入门禁；PLC重启后非保持元数据使旧事件缓冲自然失效。

**Tech Stack:** Python 3.12、pytest、pymodbus 3.14、PySide6、PyInstaller、AutoShop LiteST参考源、Easy521 Modbus TCP。

## Global Constraints

- 未通过协议版本2和 `0x12345678` 字序探针前，不得产生任何 Modbus 写入。
- `D0～D206` 协议变量必须为掉电不保持；`D2000～D4159` 的硬件保持属性不得被解释为有效事件证明。
- 旧 `D1000～D1206` 只允许出现在迁移历史说明中，不得出现在有效代码或现场录入表中。
- 反转编码固定为 `-1`，速度索引 `1/2/3/4/5` 固定对应 `1/2/4/5/10°/s`。
- 全部软件验证只使用 fake transport、模拟器和本地文件；本计划不授权PLC下载、在线写入、伺服使能或运动。
- 仓库中已有未跟踪的用户文件不得加入、删除或改写。

---

### Task 1: 用失败测试锁定新寄存器合同

**Files:**
- Modify: `pc/tests/test_registers.py`
- Modify: `pc/tests/test_modbus_client.py`
- Modify: `pc/tests/test_simulated_client.py`
- Modify: `pc/tests/test_controller.py`

**Interfaces:**
- Consumes: `turntable_control.registers.Register`、`StatusSnapshot`、fake transport。
- Produces: 新地址、协议版本2以及PLC毫秒计数异常回退时会话失效的回归合同。

- [ ] **Step 1: 修改地址断言并新增旧地址禁用断言**

```python
assert Register.MODE == 0
assert Register.BACKLASH_COMPENSATION_LO == 19
assert Register.RUN_STATE == 100
assert Register.RUN_START_TICK_MS_LO == 120
assert Register.PROTOCOL_VERSION == 200
assert Register.TIME_SYNC_RESPONSE_SEQ == 206
assert Register.EVENT_BUFFER_BASE == 2000
assert not ({*range(1000, 1207)} & {int(item) for item in Register})
```

- [ ] **Step 2: 把fake PLC探针期望改为版本2并加入重启回退场景**

```python
fake_transport.memory[Register.PROTOCOL_VERSION] = 2

def test_plc_tick_restart_invalidates_session_before_any_write(fake_transport: FakeTransport) -> None:
    from turntable_control.modbus_client import ProtocolMismatch

    client = make_client(fake_transport)
    connect(client)
    fake_transport.memory[Register.PLC_TICK_MS_HI : Register.PLC_TICK_MS_LO + 1] = list(encode_u32(50_000))
    assert client.read_status().plc_tick_ms == 50_000
    fake_transport.memory[Register.PLC_TICK_MS_HI : Register.PLC_TICK_MS_LO + 1] = list(encode_u32(10))
    with pytest.raises(ProtocolMismatch, match="restart"):
        client.read_status()
    with pytest.raises(ProtocolMismatch):
        client.send_stop()
    assert fake_transport.write_calls == []
```

- [ ] **Step 3: 运行聚焦测试并确认按预期失败**

Run: `.\.venv\Scripts\python.exe -m pytest pc/tests/test_registers.py pc/tests/test_modbus_client.py pc/tests/test_simulated_client.py pc/tests/test_controller.py -q`

Expected: FAIL，失败值仍为1000/1100/1200、协议版本仍为1，且尚无PLC重启失效逻辑。

- [ ] **Step 4: 保存红阶段证据但不提交失败状态**

在工作日志中记录失败测试名称与失败原因；保持修改在工作树中，Task 2转绿后与实现一并提交，避免主分支出现故意失败的中间提交。

---

### Task 2: 迁移电脑端地址、协议探针和PLC重启门禁

**Files:**
- Modify: `pc/src/turntable_control/registers.py`
- Modify: `pc/src/turntable_control/modbus_client.py`
- Modify: `pc/src/turntable_control/simulated_client.py`
- Modify: `pc/src/turntable_control/controller.py`

**Interfaces:**
- Consumes: Task 1的新合同。
- Produces: `Register`新地址、版本2探针、重启后必须重新连接/探针的fail-closed行为。

- [ ] **Step 1: 将Register枚举迁移到新地址**

```python
MODE = 0
BACKLASH_COMPENSATION_LO = 19
RUN_STATE = 100
RUN_START_TICK_MS_LO = 120
PROTOCOL_VERSION = 200
TIME_SYNC_RESPONSE_SEQ = 206
EVENT_BUFFER_BASE = 2000
```

两个端点之间的枚举成员必须保持现有字段声明顺序和连续地址，不插入保留洞。

- [ ] **Step 2: 将协议版本常量集中为2并修正错误文本**

```python
PROTOCOL_VERSION = 2
WORD_ORDER_PROBE = (0x1234, 0x5678)
```

连接和状态复核均使用上述常量；错误信息必须指向 `D200` 和 `D201:D202`，不得继续显示 `D1200` 或 `D1201:D1202`。

- [ ] **Step 3: 实现PLC重启检测**

客户端保存最近一次经过验证的 `plc_tick_ms`，connect时清空基线。对连续样本计算 `delta = (current - previous) & 0xFFFF_FFFF`：`delta < 0x8000_0000` 为正常前进（包含自然回绕），`delta >= 0x8000_0000` 为异常回退并判定PLC重启。重启触发关闭/失效transport会话、清除对时请求与控制器运行会话缓存、发布明确错误，并且在重新connect完成版本2探针前拒绝所有写命令。

- [ ] **Step 4: 更新模拟客户端发布版本2并模拟相同地址合同**

```python
protocol_version=2
word_order_probe=0x12345678
```

- [ ] **Step 5: 运行Task 1聚焦测试到绿色**

Run: `.\.venv\Scripts\python.exe -m pytest pc/tests/test_registers.py pc/tests/test_modbus_client.py pc/tests/test_simulated_client.py pc/tests/test_controller.py -q`

Expected: PASS。

- [ ] **Step 6: 运行全部PC测试**

Run: `$env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -m pytest pc/tests -q`

Expected: PASS，无真实网络或PLC访问。

- [ ] **Step 7: 提交电脑端迁移**

```powershell
git add pc/src/turntable_control/registers.py pc/src/turntable_control/modbus_client.py pc/src/turntable_control/simulated_client.py pc/src/turntable_control/controller.py pc/tests/test_registers.py pc/tests/test_modbus_client.py pc/tests/test_simulated_client.py pc/tests/test_controller.py
git commit -m "fix: migrate control protocol to non-retentive registers"
```

---

### Task 3: 迁移PLC参考变量和静态合同

**Files:**
- Modify: `pc/tests/test_plc_source_contract.py`
- Modify: `plc/src/Turntable_Constants.st`
- Modify: `plc/src/PRG_MAIN.st`
- Modify: `plc/README.md`
- Modify: `plc/register-map.md`

**Interfaces:**
- Consumes: Task 2的新地址与协议版本2。
- Produces: 可逐项录入AutoShop的唯一PLC参考合同。

- [ ] **Step 1: 先修改静态合同测试**

测试必须要求变量名 `iD0000Mode～iD0019BacklashLo`、`iD0100RunState～iD0120RunStartTickLo`、`iD0200ProtocolVersion～iD0206TimeSyncResponse`、`aD2000Events`，并拒绝有效源文件中的旧变量名。测试还必须要求 `PROTOCOL_VERSION := 2`。

- [ ] **Step 2: 运行PLC静态测试并确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest pc/tests/test_plc_source_contract.py -q`

Expected: FAIL，报告旧变量名和协议版本1。

- [ ] **Step 3: 重命名全局变量并更新PRG_MAIN引用**

```text
iD1000Mode -> iD0000Mode
iD1100RunState -> iD0100RunState
iD1200ProtocolVersion -> iD0200ProtocolVersion
```

每个地址块按偏移一一替换；事件数组 `aD2000Events` 不变。常量中的 `PROTOCOL_VERSION` 改为2。

- [ ] **Step 4: 重写变量表与寄存器文档**

`plc/README.md`逐行给出新变量名、INT类型、初始值、自动“不保持/私有”和软元件地址；事件数组明确为2160个INT、地址D2000、自动“保持/私有”。`plc/register-map.md`明确0基Modbus地址等于D号，并记录旧地址仅作迁移背景。

- [ ] **Step 5: 运行PLC静态合同和全部PC测试**

Run: `.\.venv\Scripts\python.exe -m pytest pc/tests/test_plc_source_contract.py -q`

Run: `$env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -m pytest pc/tests -q`

Expected: 两者均PASS。

- [ ] **Step 6: 提交PLC参考迁移**

```powershell
git add pc/tests/test_plc_source_contract.py plc/src/Turntable_Constants.st plc/src/PRG_MAIN.st plc/README.md plc/register-map.md
git commit -m "fix: publish Easy521 non-retentive PLC contract"
```

---

### Task 4: 更新交付文档并清除可执行旧地址说明

**Files:**
- Modify: `README.md`
- Modify: `docs/AI_HANDOFF.md`
- Modify: `docs/on-site-commissioning-checklist.md`
- Modify: `docs/worklog/2026-08-11-development-log.md`
- Modify: `docs/verification-report.md`
- Modify: `docs/delivery-manifest.md`

**Interfaces:**
- Consumes: Task 2和Task 3已验证的新协议。
- Produces: 现场人员只会看到新地址、版本2和正确反转编码的中文操作说明。

- [ ] **Step 1: 添加文档合同测试或扩展现有静态测试**

断言现场清单只读探针为 `D200:D202`，值为版本2和 `0x1234/0x5678`；变量录入为D0/D100/D200；反转为-1。允许设计迁移文档保留旧地址，其他面向执行的文档不得把旧地址作为当前合同。

- [ ] **Step 2: 运行测试并确认旧文档导致失败**

Run: `.\.venv\Scripts\python.exe -m pytest pc/tests/test_plc_source_contract.py pc/tests/test_smoke.py -q`

Expected: FAIL并指出旧地址或版本1。

- [ ] **Step 3: 更新中文使用说明、交接、现场清单和工作日志**

清单顺序维持：物理安全→离线编译→只读版本2/字序验证→明确授权下载→空载1°/s小位移；不得加入高速、整圈或带载批准。

- [ ] **Step 4: 运行文档聚焦测试和全部PC测试**

Run: `.\.venv\Scripts\python.exe -m pytest pc/tests/test_plc_source_contract.py pc/tests/test_smoke.py -q`

Run: `$env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -m pytest pc/tests -q`

Expected: PASS。

- [ ] **Step 5: 提交文档迁移**

```powershell
git add README.md docs/AI_HANDOFF.md docs/on-site-commissioning-checklist.md docs/worklog/2026-08-11-development-log.md docs/verification-report.md docs/delivery-manifest.md
git commit -m "docs: migrate commissioning guide to protocol v2"
```

---

### Task 5: 重建Windows交付物并做最终验证

**Files:**
- Regenerate: `dist/TurntableControl/`
- Modify: `docs/verification-report.md`
- Modify: `docs/delivery-manifest.md`
- Modify: `docs/worklog/2026-08-11-development-log.md`

**Interfaces:**
- Consumes: 全部协议v2代码和文档。
- Produces: 新EXE、可复核SHA-256、无PLC烟雾测试证据。

- [ ] **Step 1: 新鲜运行完整测试和编译检查**

```powershell
$env:QT_QPA_PLATFORM='offscreen'
.\.venv\Scripts\python.exe -m pytest pc/tests -q
.\.venv\Scripts\python.exe -m compileall -q pc/src
git diff --check
```

Expected: 全部退出码0。

- [ ] **Step 2: 运行Windows构建与无网络模拟器烟雾测试**

Run: `.\scripts\build-windows.ps1`

Expected: 输出“构建与无网络启动检查通过”，不访问PLC网络。

- [ ] **Step 3: 记录EXE证据**

```powershell
Get-Item .\dist\TurntableControl\TurntableControl.exe | Select-Object Length,LastWriteTime
Get-FileHash .\dist\TurntableControl\TurntableControl.exe -Algorithm SHA256
```

把实际大小、时间和SHA-256写入verification report、delivery manifest和worklog，不复制旧值。

- [ ] **Step 4: 再次运行包烟雾测试和完整测试**

Run: `& '.\dist\TurntableControl\TurntableControl.exe' --simulator --package-smoke --data-dir '.\data\package-smoke-v2'`

Run: `$env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -m pytest pc/tests -q`

Expected: 两者退出码0。

- [ ] **Step 5: 检查当前合同中没有旧地址依赖**

Run: `rg -n "D10(0[0-9]|1[0-9])|D11(0[0-9]|1[0-9]|20)|D120[0-6]" pc/src plc README.md docs --glob '!docs/superpowers/**'`

Expected: 无有效合同命中；若工作日志保留历史说明，逐项人工确认为过去时迁移记录。

- [ ] **Step 6: 提交交付证据**

```powershell
git add dist/TurntableControl docs/verification-report.md docs/delivery-manifest.md docs/worklog/2026-08-11-development-log.md
git commit -m "build: publish protocol v2 turntable controller"
```

---

### Task 6: AutoShop离线录入交接门禁

**Files:**
- No repository file changes unless现场结果需要追加到 `docs/worklog/2026-08-11-development-log.md`。

**Interfaces:**
- Consumes: Task 3的唯一变量表和Task 5的新EXE。
- Produces: AutoShop离线工程0错误、0警告的截图证据；不包含PLC下载。

- [ ] **Step 1: 用户把前三行改名并保存**

```text
iD0000Mode       INT 0 不保持 私有 D0
iD0001Direction  INT 1 不保持 私有 D1
iD0002SpeedIndex INT 1 不保持 私有 D2
```

- [ ] **Step 2: 按plc/README.md分块录入D3～D19、D100～D120、D200～D206和aD2000Events**

每完成一个地址块即保存并离线编译，禁止批量跨块盲录。

- [ ] **Step 3: 收集离线编译证据**

Expected: AutoShop V4.12.0.2显示0错误、0警告；D0～D206为不保持，D2000数组为保持；变量名与软元件地址一致。

- [ ] **Step 4: 到此停止并请求独立的下载授权**

不得把离线编译成功解释为已完成PLC下载或真实运动验证。下一阶段必须重新确认伺服未使能、电机静止、机械区域清空和USB通信状态。
