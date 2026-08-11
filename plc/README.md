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
`FC_SplitU32` for raw unsigned tick values. Read
and verify the D1201/D1202 `0x1234`/`0x5678` word-order probe before any hardware
write. If it differs, stop and correct the single PC codec and this contract.

## Variable table: MODBUS_D

Create one **global** variable table named `MODBUS_D` by pasting the `VAR_GLOBAL`
section in `Turntable_Constants.st`. In its AutoShop address column bind one
`INT` to each address exactly as listed. Do not redeclare these names in
`PRG_MAIN`. `NR` means non-retained and `R`
means retained; all Modbus wire words are `NR` so a restart never replays a
command. Initial values are bit patterns where `16#` notation is shown.

| Address | Variable | Type | Policy | Initial | Description |
|---|---|---|---|---|---|
| D1000 | iD1000Mode | INT | NR | 0 | MODE: 0 manual, 1 automatic |
| D1001 | iD1001Direction | INT | NR | 1 | DIRECTION: +1 CW, -1 CCW |
| D1002 | iD1002SpeedIndex | INT | NR | 1 | fixed speed index 1..5 |
| D1003 | iD1003StartSeq | INT | NR | 0 | START_SEQ bit pattern |
| D1004 | iD1004StopSeq | INT | NR | 0 | STOP_SEQ bit pattern, highest priority |
| D1005 | iD1005SetZeroSeq | INT | NR | 0 | SET_ZERO_SEQ bit pattern |
| D1006 | iD1006ResetFaultSeq | INT | NR | 0 | RESET_FAULT_SEQ bit pattern |
| D1007 | iD1007PowerSeq | INT | NR | 0 | POWER_SEQ bit pattern |
| D1008 | iD1008Heartbeat | INT | NR | 0 | HEARTBEAT bit pattern |
| D1009 | iD1009BufferAckSeq | INT | NR | 0 | BUFFER_ACK_SEQ bit pattern |
| D1010 | iD1010RatioHi | INT | NR | 0 | TOTAL_RATIO high word |
| D1011 | iD1011RatioLo | INT | NR | 16#C350 | TOTAL_RATIO low word, 50000 milli |
| D1012 | iD1012AccelHi | INT | NR | 0 | ACCELERATION high word |
| D1013 | iD1013AccelLo | INT | NR | 5000 | ACCELERATION low word, 5.000 deg/s2 |
| D1014 | iD1014DecelHi | INT | NR | 0 | DECELERATION high word |
| D1015 | iD1015DecelLo | INT | NR | 5000 | DECELERATION low word, 5.000 deg/s2 |
| D1016 | iD1016StopDecelHi | INT | NR | 0 | STOP DECELERATION high word |
| D1017 | iD1017StopDecelLo | INT | NR | 10000 | STOP DECELERATION low word, 10.000 deg/s2 |
| D1018 | iD1018BacklashHi | INT | NR | 0 | BACKLASH high word |
| D1019 | iD1019BacklashLo | INT | NR | 0 | BACKLASH low word |
| D1100 | iD1100RunState | INT | NR | 0 | PLC run state |
| D1101 | iD1101StatusFlags | INT | NR | 0 | zero/power/limit/buffer/heartbeat flags |
| D1102 | iD1102FaultCode | INT | NR | 0 | PLC fault code |
| D1103 | iD1103PositionHi | INT | NR | 0 | ACTUAL_POSITION high word |
| D1104 | iD1104PositionLo | INT | NR | 0 | ACTUAL_POSITION low word |
| D1105 | iD1105TargetHi | INT | NR | 0 | TARGET_POSITION high word |
| D1106 | iD1106TargetLo | INT | NR | 0 | TARGET_POSITION low word |
| D1107 | iD1107VelocityHi | INT | NR | 0 | ACTUAL_VELOCITY high word |
| D1108 | iD1108VelocityLo | INT | NR | 0 | ACTUAL_VELOCITY low word |
| D1109 | iD1109HeartbeatEcho | INT | NR | 0 | accepted HEARTBEAT |
| D1110 | iD1110StartAck | INT | NR | 0 | START_ACK_SEQ |
| D1111 | iD1111StopAck | INT | NR | 0 | STOP_ACK_SEQ |
| D1112 | iD1112SetZeroAck | INT | NR | 0 | SET_ZERO_ACK_SEQ |
| D1113 | iD1113ResetFaultAck | INT | NR | 0 | RESET_FAULT_ACK_SEQ |
| D1114 | iD1114PowerAck | INT | NR | 0 | POWER_ACK_SEQ |
| D1115 | iD1115BufferAcked | INT | NR | 0 | BUFFER_ACKED_SEQ |
| D1116 | iD1116EventCount | INT | NR | 0 | buffered event count, maximum 360 |
| D1117 | iD1117Generation | INT | NR | 0 | event-buffer generation |
| D1118 | iD1118RunStatus | INT | NR | 0 | final/current run status |
| D1200 | iD1200ProtocolVersion | INT | NR | 1 | protocol version 1, PLC writes |
| D1201 | iD1201WordOrderHi | INT | NR | 16#1234 | word-order probe high word |
| D1202 | iD1202WordOrderLo | INT | NR | 16#5678 | word-order probe low word |
| D1203 | iD1203TimeSyncRequest | INT | NR | 0 | PC time-sync request sequence |
| D1204 | iD1204TickHi | INT | NR | 0 | PLC_TICK_MS raw u32 high word |
| D1205 | iD1205TickLo | INT | NR | 0 | PLC_TICK_MS raw u32 low word |
| D1206 | iD1206TimeSyncResponse | INT | NR | 0 | echoed time-sync response sequence |
| D2000:D4159 | aD2000Events[0..2159] | ARRAY[0..2159] OF INT | NR | 0 | 360 records * 6 words, contiguous event array |

The retained items are deliberately **unbound**: `bZeroValid` and the retained
stop reason in `FB_TurntableControl`. Mark those FB retained variables `R` in
the POU variable editor if AutoShop requires an explicit retention flag. They
are not wire registers. Define only `udiPlcTickMs`, PLCopen feedback/command
variables, decoded internal parameters, and the two FB instances in the
`PRG_MAIN` POU variable editor; do not define another MODBUS_D table there. A
buffer is sealed after a run and is released only by
a changed `BUFFER_ACK_SEQ` after the PC has durably saved it.

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
   is 23-bit. Verify positive direction at unloaded 1 degrees/s. Do **not**
   hard-code a guessed pulse scaling; inspect AutoShop's axis-wizard scaling
   fields and use their documented meaning.

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
   Do not use `MC_ImmediateStop`; it is not used here. `MC_Stop` is only a
   controlled software deceleration and is not a physical emergency stop.

8. **Commissioning page.** With platform unloaded, start at 1 degrees/s only:
   verify axis disabled/enable, manual set zero, CW/CCW physical direction,
   software stop, heartbeat cable-disconnect stop, software limits, and one
   turn/event count. Do not use high speed or load: there is no physical
   emergency stop. The communication heartbeat reduces risk but is not a
   safety-rated watchdog.

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
physical emergency stop. D1012:D1017 are decoded as signed milli-degrees/s2,
must be positive, and are latched only when a run is accepted; writes during a
run do not change it. D1010 must equal the documented 50000 milli ratio and
match offline `Axis_0` scaling; phase 1 uses that as a configuration consistency
check, not a runtime scaling change. D1018 backlash must be zero or start is
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
- The unsigned 32-bit tick wraps after about 49.7 days. D1204:D1205 and event
  elapsed words are raw high-word-first u32 bit patterns; the PC must use
  `decode_u32`, not `decode_i32`, so values past 24.85 days do not become
  negative. Unsigned subtraction preserves elapsed time across one wrap; it is
  non-retained and is a duration, not an absolute clock.
- The 1000-scan communication watchdog is bounded but not safety-rated. A
  fault or controlled stop must be verified on the real hardware before use.
