# 天线测试转台软件验证报告

日期：2026-08-13
范围：Task 1～9 的本地软件、PLC 参考源码静态契约、模拟端到端流程与 Windows 打包。
设备访问：本报告中的验证未连接网络、PLC、AutoShop、伺服或转台硬件。

## 验证结论

本地软件链路已完成：中文界面 → 单线程控制器 → 与 Modbus 客户端同接口的确定性模拟客户端 → 平滑运动模型 → 逐度事件 → PLC/电脑时钟映射 → 耐久 CSV → 缓冲确认。

模拟验证不是硬件验收。软件停止和心跳停止都不是实体急停或安全等级功能。现场没有实体急停时，只允许空载、人员远离、1°/s 的受控初次调试。

## 自动化覆盖

- 自动模式：正转/反转 × 1、2、4、5、10°/s，共 10 种组合；每次相对运行 360°。
- 每个完整自动测试严格输出 360 条事件和 360 行 CSV，行程角为 1～360，方向位置为 ±1～±360°。
- 手动模式：正反方向均最多一圈，连续坐标限制为 -360°～+360°；同方向无剩余空间时拒绝启动。
- 平滑运动：加速/减速 5°/s²，软件停止减速度 10°/s²；完整一圈总时长允许略长于匀速理论值。
- 软件停止：手动与自动均保留 1～N 的连续逐度前缀，终态分别标记；跨过第 1°前停止时保存一条仅含运行元数据的 CSV 记录，事件字段为空，保存后再确认缓冲。
- 心跳：只有真实心跳写入才能续期；状态读取不能偷续命；超过 1 s 后进入通信中止平滑停止，重连不重发启动。
- 时间：覆盖原始 32 位 PLC 毫秒计数回绕；CSV 时间戳保持非递减。
- 数据安全：先读取、再持久保存 CSV、最后确认 PLC 缓冲；磁盘失败或未完成对时时保留缓冲，不确认，显式重试后完成。
- 界面：模拟器有“无 PLC”醒目标识；硬件模式在点击有效 IPv4 的连接按钮之前不创建 Modbus 客户端。

## 最新验证证据

2026-08-15 Task 5 最终交付验证使用项目 `.venv` 的 Python 3.12.13。共享环境的可编辑安装指向另一工作树，因此验证进程仅以 `$env:PYTHONPATH='<项目根目录>\\pc\\src'` 将当前工作树源码置于导入路径首位；未修改依赖、构建脚本或受版本控制的源码：

```powershell
$env:QT_QPA_PLATFORM='offscreen'
.\.venv\Scripts\python.exe -m pytest pc/tests/test_simulated_client.py pc/tests/test_end_to_end.py -o addopts= -q
.\.venv\Scripts\python.exe -m pytest pc/tests -o addopts= -q
.\.venv\Scripts\python.exe -m compileall -q pc/src
git diff --check
.\scripts\build-windows.ps1
& '.\dist\TurntableControl\TurntableControl.exe' --simulator --package-smoke --data-dir '.\data\package-smoke-v2'
```

最终结果：构建前和构建后均完成全量 PC 测试；构建后新鲜运行结果为 `359 passed in 7.11s`。`compileall` 与 `git diff --check` 均以退出码 0 完成；`build-windows.ps1` 输出“构建与无网络启动检查通过”；独立 `--simulator --package-smoke` 以退出码 0 完成。

Windows 构建环境：PyInstaller 6.22.0、Python 3.12.13、Windows 11。输出：

`F:\Codex_PRJ\天线平台旋转装置\.worktrees\turntable-control\dist\TurntableControl\TurntableControl.exe`

构建脚本会把当前 Python 的 `DLLs` 目录置于 `PATH` 首位，避免错误收集 Anaconda/Git 的不兼容 OpenSSL；正式包验证为 OpenSSL 3.5.7。构建后以 `--simulator --package-smoke` 启动真实 GUI 路径并在 250 ms 后自动退出，全程无 PLC/网络访问。

本次重建 EXE 为 `2,663,846` 字节，最后写入时间为 `2026-08-15 15:06:33 +08:00`，SHA-256 为 `A8FD9CA3D02190791BCCEA137225AD8F8177F9B2084A69E8F9C85F266CE733AF`。

## 已验证、静态检查与现场待验证

已模拟验证：电脑端协议规则、状态联锁、全部速度/方向流程、±360°边界、逐度事件、时间映射、CSV、停止/心跳、失败恢复、界面与 Windows 包启动。

已静态检查：Easy521 AutoShop LiteST 参考源码、协议 v2 的 `D0:D19` 命令/参数、`D100:D120` 状态/运行元数据、`D200:D206` 协议/时间同步与 `D2000:D4159` 事件缓冲，及 PLCopen 调用顺序、高字优先 32 位合同。未声称 AutoShop 编译通过。

现场待验证：

1. AutoShop 实际编译、PLC 型号/固件适配与程序下载。
2. EtherCAT PDO、轴使能、报警复位及 SV660N 参数。
3. 50:1 总减速比、轴单位缩放、编码器反馈、正反方向和人工设零。
4. Modbus TCP 地址、只读 `D200:D202` 门禁（协议版本 `2` 与 `0x1234`/`0x5678` 字序探针）及真实寄存器读写行为；方向编码反转=-1。
5. PLC 扫描周期、360 条事件最坏写入时间、网络抖动和时钟误差。
6. 机械限位、干涉、线缆缠绕、实际停止距离和空载 1°/s 运动。
7. 安装实体急停与安全回路后，才能扩大速度或负载范围。

PLC 重启后的 `D2000:D4159` 原始事件字不构成可恢复的测试记录：事件计数、代次、运行状态和缓冲就绪状态会复位，旧字无效，不得导出或确认。

时间字段的 1 ms 是字段分辨率，不是绝对时间精度保证；后续与 KC908A 对齐时还需测量两套系统的时钟偏差与漂移。

现场执行时使用 `docs/on-site-commissioning-checklist.md` 逐项记录。在操作员不在现场、PLC 未实际连接的当前阶段，Task 10 的所有硬件项目保持“待验证”，本报告不填写虚构结果。
