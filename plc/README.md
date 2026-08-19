# Easy521 AutoShop LiteST reference set

This directory is a pasteable **reference source set**, not an AutoShop project
and not evidence of a successful compilation or download. AutoShop stores POU
declarations, variable tables, axis instances, and source bodies separately.
The installed AutoShop compiler remains the final syntax/type authority: adapt
only documented type-name or call-signature differences, record the version and
then compile offline to zero errors before considering a download.

`register-map.md` is the sole normative Modbus address contract. Every wire
register below is one `INT`; do not bind a `DINT` or `REAL` directly to D words.
Use `FC_DecodeI32`/`FC_SplitI32` for high-word-first signed 32-bit values and
`FC_SplitU32` for raw unsigned tick values. The D0200:D0202 probe is a
post-download read-only gate before PC Modbus writes; it is not a prerequisite
for the initial user-authorized USB download to an old or empty PLC.

### AutoShop V4.12 type and initial-value note

AutoShop V4.12 does not support `UINT` or `UDINT` in the operator tables. Use
`INT` for every 16-bit wire value, including values represented as unsigned
bit patterns. Enter these initial values in decimal:

```text
D0011 = -15536
D0201 = 4660
D0202 = 22136
```

Offline AutoShop compilation is an operator action. It must finish with `0
error` and `0 warning` before any request to download; this reference set does
not claim that compilation has been performed.

## Variable table: MODBUS_D

Create one **global** variable table named `MODBUS_D` by pasting the `VAR_GLOBAL`
section in `Turntable_Constants.st`. In its AutoShop address column bind one
`INT` to each address exactly as listed. Do not redeclare these names in
`PRG_MAIN`. Bind the address, then verify AutoShop automatically displays non-retained/private
for D0:D206 and retained/private for D2000:D4159. Do not override the fixed
address-derived property. After a PLC restart, raw D2000:D4159 words may persist, but D116 EVENT_COUNT,
D117 EVENT_GENERATION, D118 RUN_STATUS, and the buffer-ready status flag reset. The old words are invalid and must never be exported or acknowledged.
Initial values are bit patterns where `16#` notation is shown.

| Address | Variable | Type | AutoShop property | Initial | Description |
|---|---|---|---|---|---|
| D0000 | iD0000Mode | INT | non-retained / private | 0 | MODE: 0 manual, 1 automatic |
| D0001 | iD0001Direction | INT | non-retained / private | 1 | DIRECTION: +1 CW, -1 CCW |
| D0002 | iD0002SpeedIndex | INT | non-retained / private | 1 | fixed speed index 1..5 |
| D0003 | iD0003StartSeq | INT | non-retained / private | 0 | START_SEQ bit pattern |
| D0004 | iD0004StopSeq | INT | non-retained / private | 0 | STOP_SEQ bit pattern, highest priority |
| D0005 | iD0005SetZeroSeq | INT | non-retained / private | 0 | SET_ZERO_SEQ bit pattern |
| D0006 | iD0006ResetFaultSeq | INT | non-retained / private | 0 | RESET_FAULT_SEQ bit pattern |
| D0007 | iD0007PowerSeq | INT | non-retained / private | 0 | POWER_SEQ bit pattern |
| D0008 | iD0008Heartbeat | INT | non-retained / private | 0 | HEARTBEAT bit pattern |
| D0009 | iD0009BufferAckSeq | INT | non-retained / private | 0 | BUFFER_ACK_SEQ bit pattern |
| D0010 | iD0010RatioHi | INT | non-retained / private | 0 | TOTAL_RATIO high word |
| D0011 | iD0011RatioLo | INT | non-retained / private | 16#C350 | TOTAL_RATIO low word, 50000 milli |
| D0012 | iD0012AccelHi | INT | non-retained / private | 0 | ACCELERATION high word |
| D0013 | iD0013AccelLo | INT | non-retained / private | 5000 | ACCELERATION low word, 5.000 deg/s2 |
| D0014 | iD0014DecelHi | INT | non-retained / private | 0 | DECELERATION high word |
| D0015 | iD0015DecelLo | INT | non-retained / private | 5000 | DECELERATION low word, 5.000 deg/s2 |
| D0016 | iD0016StopDecelHi | INT | non-retained / private | 0 | STOP DECELERATION high word |
| D0017 | iD0017StopDecelLo | INT | non-retained / private | 10000 | STOP DECELERATION low word, 10.000 deg/s2 |
| D0018 | iD0018BacklashHi | INT | non-retained / private | 0 | BACKLASH high word |
| D0019 | iD0019BacklashLo | INT | non-retained / private | 0 | BACKLASH low word |
| D0100 | iD0100RunState | INT | non-retained / private | 0 | PLC run state |
| D0101 | iD0101StatusFlags | INT | non-retained / private | 0 | zero/power/limit/buffer/heartbeat flags |
| D0102 | iD0102FaultCode | INT | non-retained / private | 0 | PLC fault code |
| D0103 | iD0103PositionHi | INT | non-retained / private | 0 | ACTUAL_POSITION high word |
| D0104 | iD0104PositionLo | INT | non-retained / private | 0 | ACTUAL_POSITION low word |
| D0105 | iD0105TargetHi | INT | non-retained / private | 0 | TARGET_POSITION high word |
| D0106 | iD0106TargetLo | INT | non-retained / private | 0 | TARGET_POSITION low word |
| D0107 | iD0107VelocityHi | INT | non-retained / private | 0 | ACTUAL_VELOCITY high word |
| D0108 | iD0108VelocityLo | INT | non-retained / private | 0 | ACTUAL_VELOCITY low word |
| D0109 | iD0109HeartbeatEcho | INT | non-retained / private | 0 | accepted HEARTBEAT |
| D0110 | iD0110StartAck | INT | non-retained / private | 0 | START_ACK_SEQ |
| D0111 | iD0111StopAck | INT | non-retained / private | 0 | STOP_ACK_SEQ |
| D0112 | iD0112SetZeroAck | INT | non-retained / private | 0 | SET_ZERO_ACK_SEQ |
| D0113 | iD0113ResetFaultAck | INT | non-retained / private | 0 | RESET_FAULT_ACK_SEQ |
| D0114 | iD0114PowerAck | INT | non-retained / private | 0 | POWER_ACK_SEQ |
| D0115 | iD0115BufferAcked | INT | non-retained / private | 0 | BUFFER_ACKED_SEQ |
| D0116 | iD0116EventCount | INT | non-retained / private | 0 | buffered event count, maximum 360 |
| D0117 | iD0117Generation | INT | non-retained / private | 0 | event-buffer generation |
| D0118 | iD0118RunStatus | INT | non-retained / private | 0 | final/current run status |
| D0119 | iD0119RunStartTickHi | INT | non-retained / private | 0 | RUN_START_TICK_MS raw u32 high word |
| D0120 | iD0120RunStartTickLo | INT | non-retained / private | 0 | RUN_START_TICK_MS raw u32 low word |
| D0200 | iD0200ProtocolVersion | INT | non-retained / private | 2 | protocol version 2, PLC writes |
| D0201 | iD0201WordOrderHi | INT | non-retained / private | 16#1234 | word-order probe high word |
| D0202 | iD0202WordOrderLo | INT | non-retained / private | 16#5678 | word-order probe low word |
| D0203 | iD0203TimeSyncRequest | INT | non-retained / private | 0 | PC time-sync request sequence |
| D0204 | iD0204TickHi | INT | non-retained / private | 0 | PLC_TICK_MS raw u32 high word |
| D0205 | iD0205TickLo | INT | non-retained / private | 0 | PLC_TICK_MS raw u32 low word |
| D0206 | iD0206TimeSyncResponse | INT | non-retained / private | 0 | echoed time-sync response sequence |
| D2000:D4159 | aD2000Events[0..2159] | ARRAY[0..2159] OF INT | retained / private | 0 | 2160 INT words: 360 records * 6 contiguous words |

The retained internal items are deliberately **unbound**: `bZeroValid` and the
retained stop reason in `FB_TurntableControl`. They are not wire registers;
`aD2000Events` is the retained/private bound event array. Define only
`udiPlcTickMs`, PLCopen feedback/command
variables, decoded internal parameters, and the two FB instances in the
`PRG_MAIN` POU variable editor; do not define another MODBUS_D table there. A
buffer is sealed after a run and is released only by a changed `BUFFER_ACK_SEQ`
after the PC has durably saved it without a PLC restart.

## Page-by-page AutoShop configuration

1. **Project page.** Create or select an `Easy521-0808TN` project, immediately
   save a separate backup, then record the PLC firmware and AutoShop version.
   Confirm firmware/AutoShop compatibility from the installed tools before
   proceeding; this reference cannot make that compatibility decision.

2. **Network page.** Keep ordinary PC-to-PLC Ethernet on `EtherNET1` for
   Modbus TCP. Keep the separate EtherCAT `CN3` to the SV660N on its motion
   network. The Type-C port is programming only, not the Modbus TCP or EtherCAT
   motion path.

3. **EtherCAT page.** Scan/import the SV660N and create `Axis_0`. Inspect the
   PDO mapping, including outputs `6040h`, `607Ah`, `6081h`, `6083h`, `6084h`,
   and `6060h`; inputs `6041h`, `6064h`, and `6061h`; plus every
   AutoShop-required default PDO. Do not assume a scan alone made these PDOs
   valid. Record the result in commissioning evidence.

4. **Axis page.** Configure `Axis_0` as a linear axis/user-unit representation
   so calls use turntable degrees and degrees/s (not motor turns). The total
   ratio is `50:1`: 5:1 gears times a 10:1 planetary reducer. The motor encoder
   is 23-bit. Inspect the configured positive-direction mapping in the Axis
   page only; do not enable or command physical motion during this offline
   configuration. Do **not** hard-code a guessed pulse scaling; inspect
   AutoShop's axis-wizard scaling fields and use their documented meaning.

5. **Limit/task page.** Set position software limits to -360 and +360 degrees.
   Set the EtherCAT period to 1 ms and schedule `PRG_MAIN` at a constant 1 ms
   scan. One ms is the event record resolution, not a guaranteed absolute
   timestamp accuracy. During commissioning measure scan overrun and jitter;
   do not infer timing accuracy from a nominal task period.

6. **Variables/POUs page.** Create the one global `MODBUS_D` table from the
   `VAR_GLOBAL` declarations and table above, binding the D2000 event array
   continuously through D4159. Add global constants from
   `Turntable_Constants.st`, then FCs from `Turntable_RegisterCodec.st`, then
   FBs `FB_DegreeLogger` and `FB_TurntableControl`, then `PRG_MAIN`. Create its
   `fbControl`/`fbLogger` instances and the configured `Axis_0` instance before
   pasting bodies. Compile offline until zero errors. Record compiler/firmware
   versions and every type-name adaptation; the installed compiler is the final
   authority and this source set does not claim a compilation result.

7. **Download page.** Download only while the user is present and documented
   safety preconditions are met. Leave AutoShop online axis-debug mode before
   issuing PLC motion commands: concurrent online axis debug causes error 9116.
   Keep the servo disabled and stationary, disconnect the PC Modbus client, and
   permit no motion during the download. After that download, reconnect read-only and verify D0200 is `2` and D0201/D0202 are `0x1234`/`0x5678` before any PC Modbus write.
   Do not use `MC_ImmediateStop`; it is not used here. `MC_Stop` is only a
   controlled software deceleration and is not a physical emergency stop.

8. **Commissioning page.** With platform unloaded, use 1 degrees/s only and
   make an approximately 1 degree CW/CCW small displacement, stopping
   immediately. Verify axis disabled/enable, manual set zero, CW/CCW physical
   direction, software stop, heartbeat cable-disconnect stop, and software
   limits. Full-turn/event-count hardware verification is not approved in this phase and needs a separately approved future procedure; simulator-only
   full-turn/event-count checks may remain. Do not use high speed or load:
   there is no physical emergency stop. The communication heartbeat reduces
   risk but is not a safety-rated watchdog.

## PLCopen and command behavior

The supplied `PRG_MAIN` uses the LiteST manual-confirmed pin names. Motion
inputs latch on an `Execute` rising edge; positive/negative `Distance` selects
direction; `CurveType=0` is trapezoidal. `MC_Stop.Done=TRUE` while Execute is
still true holds the axis in Stopping. The program resets `bStopExecute` after
Done **or Error** so a failed Stop instruction cannot remain concurrently
asserted with `MC_Reset`; an Error is still an unsafe terminal fault and does
not confirm standstill or permit automatic restart. It intentionally has no
immediate-stop/emergency-stop behavior.

Stop sequence changes are checked before starts. All sequence and heartbeat
registers are 16-bit bit patterns; `<>` detects a changed value across wrap.
The heartbeat age faults a running session only when it is strictly greater
than 1000 scans. Stop reason is retained. A manual target clamps
`position +/- 360` to global -360/+360; automatic motion is an exact relative
`+360` or `-360` and is rejected if its target would exceed the global limit.
Set zero requires no active move/stop, valid feedback, no power fault, and near
zero velocity. Invalid direction/mode/speed, read errors, power/motion errors,
automatic-limit rejection, and communication timeout all publish a fault or
terminal status; no command waits indefinitely.

`TARGET_POSITION` is the controller's latched accepted target, not actual
position plus a live distance. It remains available after normal completion;
after a controlled stop reports Done it is updated to the final actual position.
A STOP sequence also acknowledges/discards a pending START sequence, so a
same-scan start cannot launch on the following scan. A position/velocity/motion
feedback fault while moving latches a controlled `MC_Stop` and only seals the
run after `MC_Stop.Done`. If `MC_Stop.Error` occurs, the program inhibits its
power-enable output, publishes `FAULT_STOP_UNSAFE`, and does not seal or clear
the event buffer automatically. On site: isolate energy using the approved
machine procedure, prevent access, investigate axis/drive state, and do not
resume or acknowledge data until the cause and safe standstill are verified.

`RESET_FAULT_SEQ` starts `MC_Reset`; it is intentionally accepted even for an
axis/motion error while no run is active. `RESET_FAULT_ACK_SEQ` changes only
after the instruction returns Done or Error. This is not a substitute for a
physical emergency stop. D0012:D0017 are decoded as signed milli-degrees/s2,
must be positive, and are latched only when a run is accepted; writes during a
run do not change it. D0010 must equal the documented 50000 milli ratio and
match offline `Axis_0` scaling; phase 1 uses that as a configuration consistency
check, not a runtime scaling change. D0018 backlash must be zero or start is
rejected, because phase 1 has no verified compensation algorithm.

## Bounded resource and timing summary

- The event table is fixed at 2160 `INT` words (360 * 6): 4320 bytes on a
  16-bit `INT` target, plus bounded control state. No dynamic allocation is
  used.
- `FB_DegreeLogger` has one fixed `FOR ... TO 360` crossing guard and retains
  every legal crossing after a discontinuity. At normal 10 degrees/s with a
  1 ms scan, no more than one degree is crossed in 100 scans. A 360-write
  discontinuity can overrun a 1 ms task, so measure its worst case on target
  during commissioning and treat a scan overrun as a commissioning failure.
- Keep task/FB nesting shallow: one 1 ms `PRG_MAIN` task with two FB calls and
  two codec FCs. Check scan-overrun/jitter with AutoShop commissioning tools.
- The unsigned 32-bit tick wraps after about 49.7 days. D0119:D0120
  `RUN_START_TICK_MS`, D0204:D0205, and event elapsed words are raw
  high-word-first u32 bit patterns; the PC must use
  `decode_u32`, not `decode_i32`, so values past 24.85 days do not become
  negative. Unsigned subtraction preserves elapsed time across one wrap; it is
  non-retained and is a duration, not an absolute clock.
- The 1000-scan communication watchdog is bounded but not safety-rated. A
  fault or controlled stop must be verified on the real hardware before use.

## On-site record

Use `docs/on-site-commissioning-checklist.md` as the controlled record for
read-only network/protocol checks and the unloaded 1 degree/s first movement.
Nothing in this reference source or its static tests is an AutoShop compile,
download, direction, scaling, stopping-distance, or physical-safety result.
