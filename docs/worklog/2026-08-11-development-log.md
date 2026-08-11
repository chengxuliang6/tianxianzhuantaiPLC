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
- 实现 `__version__ = "0.1.0"` 后，以可编辑安装方式运行 `pc/tests/test_smoke.py`，结果为 `1 passed`；该次本地验证使用 Python 3.11 并显式跳过项目的 Python 版本限制，不能替代 Python 3.12 的正式验证。

## 风险与限制

- 当前工作机仅发现 Python 3.10 与 Python 3.11；项目规定 Python 3.12，尚不能在该机按正式依赖约束创建完整开发环境。
- 本任务未连接 PLC，未执行任何硬件读写或运动操作。
- 控制台入口指向后续任务将提供的 `turntable_control.main:main`。

## 下一步

准备 Python 3.12 环境后运行 `scripts/setup.ps1`，并继续实现寄存器协议和纯运动规则。

## 额度检查

未执行额度查询；本任务不涉及用量阈值或外部服务调用。
