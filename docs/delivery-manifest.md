# 转台控制项目交付索引

## 操作员入口

- `README.md`：安装、接线、安全边界、模拟器和硬件界面使用方法。
- `scripts/run-simulator.ps1`：无 PLC 本地演示。
- `scripts/run-app.ps1`：真实 PLC 界面启动入口；需要现场确认 IPv4。
- `docs/on-site-commissioning-checklist.md`：现场只读核验和空载 1°/s 清单。
- 现场协议为 v2：`D0:D19` 命令/参数、`D100:D120` 状态/运行元数据、`D200:D206` 协议/时间同步、`D2000:D4159` 原始事件字。现场先只读 `D200:D202`，确认版本 `2` 和 `0x1234`/`0x5678`；该门禁不通过不得写入、下载或运动。
- 方向编码为正转 `+1`、反转=-1。PLC 重启后旧 `D2000:D4159` 事件字无效，不得导出或确认。

## 工程与协议

- `plc/README.md`：Easy521 AutoShop 逐页录入、变量表和轴配置说明。
- `plc/register-map.md`：Modbus D 寄存器合同。
- `plc/src/`：LiteST 参考源码；尚未声称在现场 AutoShop 编译通过。
- `pc/src/turntable_control/`：Windows 客户端、控制器、对时、CSV、界面和模拟器源码。

## 验证与追溯

- `docs/verification-report.md`：已模拟验证、静态检查和现场待验证的明确分界。
- `docs/worklog/2026-08-11-development-log.md`：Task 1～9 的开发与复审日志。
- `pc/tests/`：自动化测试；Task 5 重建后的全量离线基线为 359 项通过。
- `scripts/build-windows.ps1`：可复现 one-directory Windows 打包与无网络模拟器 smoke。

本地构建产物（未提交 Git）：

`dist\TurntableControl\TurntableControl.exe`

Task 5 重建（2026-08-15 15:06:33 +08:00）：`TurntableControl.exe` 为 `2,663,846` 字节，SHA-256 为 `A8FD9CA3D02190791BCCEA137225AD8F8177F9B2084A69E8F9C85F266CE733AF`。该本地 `dist/` 产物被 Git 忽略，未提交；源码变化后必须重新构建并更新哈希，不能继续分发旧包。

## 当前完成边界

Task 1～9 已完成本地实现、测试、打包和独立复审。Task 10 的真实 PLC 网络、AutoShop 编译、方向/缩放及运动只能在操作员回到现场、PLC 实际连接且安全前提满足后执行；目前全部标记为待验证。
