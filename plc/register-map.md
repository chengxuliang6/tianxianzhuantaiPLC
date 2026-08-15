# 转台 Modbus 保持寄存器契约

本文件是电脑端与 Easy521 PLC 间 Modbus TCP 寄存器的唯一规范合同。除非先同步修改本文件和 `pc/src/turntable_control/registers.py` 及其测试，任何程序不得另行分配或解释寄存器。

## 地址、单位和多字编码

- Easy521 Modbus TCP 服务器使用 **0 基 holding-register 地址**；地址数值直接等于本表的 D 寄存器号。例如 `D0` 以 Modbus 地址 `0` 访问，不使用 `40001` 偏移。
- 所有角度、角速度、加速度、减速度和间隙补偿均为有符号 32 位整数，工程值放大 1000 倍。
- 有符号 32 位整数采用二进制补码、两个无符号 16 位寄存器，**高字在前、低字在后**。
- PLC 在 `D201:D202` 固定写入字序探针 `0x12345678`，即高字 `0x1234`、低字 `0x5678`。**硬件写入前必须读取并验证该魔数的实际字序。**软件只允许通过 `registers.py` 的单一适配器进行 32 位编码；若现场字序不同，先停止硬件写入并修正该适配器与本合同，再重新运行协议测试。

## D0–D19：命令与参数

| D 地址 | 名称 | 类型/单位 | 说明 |
|---:|---|---|---|
| 0 | MODE | u16 | `0=MANUAL`, `1=AUTO` |
| 1 | DIRECTION | i16 | `1=CW`, `-1=CCW` |
| 2 | SPEED_INDEX | u16 | 五档速度的 1–5 索引 |
| 3 | START_SEQ | u16 | 递增启动命令序号 |
| 4 | STOP_SEQ | u16 | 递增停止命令序号，优先处理 |
| 5 | SET_ZERO_SEQ | u16 | 递增当前位置设零命令序号 |
| 6 | RESET_FAULT_SEQ | u16 | 递增故障复位命令序号 |
| 7 | POWER_SEQ | u16 | 递增伺服使能/断使能命令序号 |
| 8 | HEARTBEAT | u16 | 电脑周期递增心跳 |
| 9 | BUFFER_ACK_SEQ | u16 | 电脑成功保存事件后的递增确认序号 |
| 10:11 | TOTAL_RATIO | i32 ×1000 | 总传动比，默认 `50000` |
| 12:13 | ACCELERATION | i32 ×1000 °/s² | 默认 `5000` |
| 14:15 | DECELERATION | i32 ×1000 °/s² | 默认 `5000` |
| 16:17 | SOFTWARE_STOP_DECELERATION | i32 ×1000 °/s² | 默认 `10000` |
| 18:19 | BACKLASH_COMPENSATION | i32 ×1000 ° | 默认 `0` |

## D100–D120：状态与运行元数据

| D 地址 | 名称 | 类型/单位 | 说明 |
|---:|---|---|---|
| 100 | RUN_STATE | u16 | PLC 状态机状态 |
| 101 | STATUS_FLAGS | u16 bit field | 零点、伺服、限位、缓冲等状态位 |
| 102 | FAULT_CODE | u16 | PLC 故障码，`0` 表示无故障 |
| 103:104 | ACTUAL_POSITION | i32 ×1000 ° | 当前连续位置 |
| 105:106 | TARGET_POSITION | i32 ×1000 ° | 当前运动目标 |
| 107:108 | ACTUAL_VELOCITY | i32 ×1000 °/s | 当前实际速度 |
| 109 | HEARTBEAT_ECHO | u16 | PLC 最后确认的电脑心跳 |
| 110 | START_ACK_SEQ | u16 | 最后确认的启动序号 |
| 111 | STOP_ACK_SEQ | u16 | 最后确认的停止序号 |
| 112 | SET_ZERO_ACK_SEQ | u16 | 最后确认的设零序号 |
| 113 | RESET_FAULT_ACK_SEQ | u16 | 最后确认的故障复位序号 |
| 114 | POWER_ACK_SEQ | u16 | 最后确认的使能序号 |
| 115 | BUFFER_ACKED_SEQ | u16 | PLC 已确认的缓冲释放序号 |
| 116 | EVENT_COUNT | u16 | 当前保留事件数量，最大 360 |
| 117 | EVENT_GENERATION | u16 | 每次新测试递增的缓冲代数 |
| 118 | RUN_STATUS | u16 | 本次测试结果状态 |
| 119:120 | RUN_START_TICK_MS | raw u32 ms | PLC 接受本次运行时锁存的单调毫秒计数；高字在前，PC 必须按 u32 解码 |

## D200–D206：时间同步与协议探针

| D 地址 | 名称 | 类型/单位 | 说明 |
|---:|---|---|---|
| 200 | PROTOCOL_VERSION | u16 | 本合同版本，初始为 `2` |
| 201:202 | WORD_ORDER_PROBE | i32 | 固定 `0x12345678`，先高后低 |
| 203 | TIME_SYNC_REQUEST_SEQ | u16 | 电脑发起的时间同步采样序号 |
| 204:205 | PLC_TICK_MS | raw u32 ms | PLC 单调毫秒计数快照；高字在前，PC 必须按 u32 解码 |
| 206 | TIME_SYNC_RESPONSE_SEQ | u16 | 与快照对应的已响应采样序号 |

## D2000–D4159：逐度事件缓冲区

缓冲区有 360 条记录，每条严格占 6 个寄存器，起始地址为 `D2000 + 6 × record_index`（`record_index=0..359`）。最后一条记录占 `D4154:D4159`，因此整个区域不与其他区域重叠。

| 相对字偏移 | 字段 | 类型/单位 |
|---:|---|---|
| 0 | sequence | u16，事件序号 |
| 1 | travel_angle | u16 °，范围 1–360 |
| 2:3 | actual_position | i32 ×1000 °，高字在前 |
| 4:5 | elapsed_ms | raw u32 ms，高字在前；PC 必须按 u32 解码 |

PLC 在电脑以 `BUFFER_ACK_SEQ` 确认成功持久化前保留这些记录，下一次测试不得静默覆盖未确认缓冲区。

## Migration background (protocol v1 only)

The obsolete v1 mapping used D1000:D1019, D1100:D1120, and D1200:D1206. It is retained here only to help migrate existing installations; it is not a current address assignment and must not be entered in AutoShop or used by PC software.
