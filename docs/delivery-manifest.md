# 转台控制项目交付索引

## 操作员入口

- `README.md`：安装、接线、安全边界、模拟器和硬件界面使用方法。
- `scripts/run-simulator.ps1`：无 PLC 本地演示。
- `scripts/run-app.ps1`：真实 PLC 界面启动入口；需要现场确认 IPv4。
- `docs/on-site-commissioning-checklist.md`：现场只读核验和空载 1°/s 清单。

## 工程与协议

- `plc/README.md`：Easy521 AutoShop 逐页录入、变量表和轴配置说明。
- `plc/register-map.md`：Modbus D 寄存器合同。
- `plc/src/`：LiteST 参考源码；尚未声称在现场 AutoShop 编译通过。
- `pc/src/turntable_control/`：Windows 客户端、控制器、对时、CSV、界面和模拟器源码。

## 验证与追溯

- `docs/verification-report.md`：已模拟验证、静态检查和现场待验证的明确分界。
- `docs/worklog/2026-08-11-development-log.md`：Task 1～9 的开发与复审日志。
- `pc/tests/`：自动化测试；Task 9 最终基线为 348 项通过。
- `scripts/build-windows.ps1`：可复现 one-directory Windows 打包与无网络模拟器 smoke。

本地构建产物（未提交 Git）：

`dist\TurntableControl\TurntableControl.exe`

Task 9 构建 SHA-256：`731411974023045BD2B11BB51C095F8B0F8F340C50E987F180C4A5C1FB1574C8`。源码变化后必须重新构建并更新哈希，不能继续分发旧包。

## 当前完成边界

Task 1～9 已完成本地实现、测试、打包和独立复审。Task 10 的真实 PLC 网络、AutoShop 编译、方向/缩放及运动只能在操作员回到现场、PLC 实际连接且安全前提满足后执行；目前全部标记为待验证。
