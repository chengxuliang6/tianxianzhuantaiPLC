(******************************************************************************
  AutoShop LiteST reference source -- create program POU PRG_MAIN and paste the
  declaration/body. Create Axis_0 in AutoShop's axis configuration, not here.
  Create the MODBUS_D variable table described in plc/README.md before binding.
******************************************************************************)
PROGRAM PRG_MAIN
VAR
    (* Bind each of these INTs to its named D word per plc/README.md. *)
    iD1000Mode, iD1001Direction, iD1002SpeedIndex : INT;
    iD1003StartSeq, iD1004StopSeq, iD1005SetZeroSeq, iD1006ResetFaultSeq : INT;
    iD1007PowerSeq, iD1008Heartbeat, iD1009BufferAckSeq : INT;
    iD1010RatioHi, iD1011RatioLo, iD1012AccelHi, iD1013AccelLo : INT;
    iD1014DecelHi, iD1015DecelLo, iD1016StopDecelHi, iD1017StopDecelLo : INT;
    iD1018BacklashHi, iD1019BacklashLo : INT;
    iD1100RunState, iD1101StatusFlags, iD1102FaultCode : INT;
    iD1103PositionHi, iD1104PositionLo, iD1105TargetHi, iD1106TargetLo : INT;
    iD1107VelocityHi, iD1108VelocityLo, iD1109HeartbeatEcho : INT;
    iD1110StartAck, iD1111StopAck, iD1112SetZeroAck, iD1113ResetFaultAck : INT;
    iD1114PowerAck, iD1115BufferAcked, iD1116EventCount, iD1117Generation : INT;
    iD1118RunStatus, iD1200ProtocolVersion, iD1201WordOrderHi, iD1202WordOrderLo : INT;
    iD1203TimeSyncRequest, iD1204TickHi, iD1205TickLo, iD1206TimeSyncResponse : INT;
    aD2000Events : ARRAY[0..2159] OF INT; (* bind D2000:D4159, never DINT *)
    udiPlcTickMs : UDINT;
    rActualPosition, rActualVelocity : REAL;
    rMoveDistance, rSelectedVelocity, rAcceleration, rDeceleration, rStopDeceleration : REAL;
    bPowerEnable, bPowerStatus, bPowerBusy, bPowerError : BOOL;
    iPowerErrorID : INT;
    bSetPositionExecute, bSetPositionDone, bSetPositionBusy, bSetPositionError : BOOL;
    iSetPositionErrorID : INT;
    bMoveExecute, bMoveDone, bMoveBusy, bMoveAborted, bMoveError : BOOL;
    iMoveErrorID : INT;
    bStopExecute, bStopDone, bStopBusy, bStopError : BOOL;
    iStopErrorID : INT;
    bPositionValid, bPositionBusy, bPositionError : BOOL;
    iPositionErrorID : INT;
    bVelocityValid, bVelocityBusy, bVelocityError : BOOL;
    iVelocityErrorID : INT;
    rPreviousPosition : REAL;
    bLoggerStarted, bLoggerFinished, bLoggerAck : BOOL;
    bLoggerBufferReady : BOOL;
    uiLoggerEventCount, uiLoggerGeneration : UINT;
    diPositionMdeg, diTargetMdeg, diVelocityMdegS : DINT;
    diTickMs : DINT;
    iUnusedHi, iUnusedLo : INT;
    fbControl : FB_TurntableControl;
    fbLogger : FB_DegreeLogger;
END_VAR

(* PRG_MAIN must be scheduled as a constant 1 ms task; tick is not retained. *)
udiPlcTickMs := udiPlcTickMs + 1;

(* 1. Refresh feedback first. These enables remain TRUE for continuously valid reads. *)
MC_ReadActualPosition(Enable := TRUE, Axis := Axis_0,
    Valid => bPositionValid, Busy => bPositionBusy,
    Position => rActualPosition,
    Error => bPositionError, ErrorID => iPositionErrorID);

MC_ReadActualVelocity(Enable := TRUE, Axis := Axis_0,
    Valid => bVelocityValid, Busy => bVelocityBusy,
    Velocity => rActualVelocity,
    Error => bVelocityError, ErrorID => iVelocityErrorID);

(* 2. Deterministic command/state decision uses current feedback and raw INT wire words. *)
fbControl(
    uiMode := UINT(iD1000Mode), iDirection := iD1001Direction,
    uiSpeedIndex := UINT(iD1002SpeedIndex), uiStartSeq := UINT(iD1003StartSeq),
    uiStopSeq := UINT(iD1004StopSeq), uiSetZeroSeq := UINT(iD1005SetZeroSeq),
    uiResetFaultSeq := UINT(iD1006ResetFaultSeq), uiPowerSeq := UINT(iD1007PowerSeq),
    uiHeartbeat := UINT(iD1008Heartbeat), uiBufferAckSeq := UINT(iD1009BufferAckSeq),
    udiNowTickMs := udiPlcTickMs,
    rActualPositionDeg := rActualPosition, rActualVelocityDegS := rActualVelocity,
    bPositionValid := bPositionValid, bVelocityValid := bVelocityValid,
    bPositionReadError := bPositionError, bVelocityReadError := bVelocityError,
    bPowerStatus := bPowerStatus, bPowerError := bPowerError, bMoveDone := bMoveDone,
    bMoveBusy := bMoveBusy, bMoveAborted := bMoveAborted, bMoveError := bMoveError,
    bStopDone := bStopDone, bStopBusy := bStopBusy, bStopError := bStopError,
    bSetPositionDone := bSetPositionDone, bSetPositionError := bSetPositionError,
    bLoggerBufferReady := bLoggerBufferReady, uiLoggerEventCount := uiLoggerEventCount);

bPowerEnable := fbControl.bPowerEnable;
rMoveDistance := fbControl.rMoveDistance; rSelectedVelocity := fbControl.rSelectedVelocity;
rAcceleration := fbControl.rAcceleration; rDeceleration := fbControl.rDeceleration;
rStopDeceleration := fbControl.rStopDeceleration;

(* 3. Rising-edge commands. Motion parameters latch on Execute's rising edge. *)
bSetPositionExecute := fbControl.bSetZeroRequest;
MC_SetPosition(Execute := bSetPositionExecute, Axis := Axis_0,
    Position := 0.0, Mode := 0,
    Done => bSetPositionDone, Busy => bSetPositionBusy,
    Error => bSetPositionError, ErrorID => iSetPositionErrorID);
bSetPositionExecute := FALSE;

bMoveExecute := fbControl.bMoveRequest;
MC_MoveRelative(Execute := bMoveExecute, Axis := Axis_0,
    Distance := rMoveDistance, Velocity := rSelectedVelocity,
    Acceleration := rAcceleration, Deceleration := rDeceleration,
    CurveType := 0, Done => bMoveDone, Busy => bMoveBusy,
    CommandAborted => bMoveAborted,
    Error => bMoveError, ErrorID => iMoveErrorID);
bMoveExecute := FALSE;

IF fbControl.bStopRequest THEN
    bStopExecute := TRUE;
END_IF;
(* MC_ImmediateStop is not used: this is a controlled software deceleration. *)
MC_Stop(Execute := bStopExecute, Axis := Axis_0,
    Deceleration := rStopDeceleration, CurveType := 0,
    Done => bStopDone, Busy => bStopBusy,
    Error => bStopError, ErrorID => iStopErrorID);
(* Done with Execute TRUE holds Stopping; reset only after Done permits Standstill. *)
IF bStopDone THEN
    bStopExecute := FALSE;
END_IF;

MC_Power(Enable := bPowerEnable, Axis := Axis_0,
    Status => bPowerStatus, Busy => bPowerBusy,
    Error => bPowerError, ErrorID => iPowerErrorID);

(* 4. Capture all crossed travel integers, then publish a stable register snapshot. *)
bLoggerStarted := fbControl.bRunStarted; bLoggerFinished := fbControl.bRunFinished;
bLoggerAck := fbControl.bBufferAckEdge;
fbLogger(rPreviousPositionDeg := rPreviousPosition, rCurrentPositionDeg := rActualPosition,
    rRunOriginDeg := fbControl.rRunOriginDeg, iDirection := fbControl.iRunDirection,
    udiRunStartTickMs := fbControl.udiRunStartTickMs, udiNowTickMs := udiPlcTickMs,
    bRunStarted := bLoggerStarted, bRunFinished := bLoggerFinished, bBufferAckEdge := bLoggerAck,
    aEventWords := aD2000Events, bBufferReady => bLoggerBufferReady,
    uiEventCount => uiLoggerEventCount, uiGeneration => uiLoggerGeneration);
rPreviousPosition := rActualPosition;

iD1100RunState := INT(fbControl.uiRunState); iD1101StatusFlags := INT(fbControl.uiStatusFlags);
iD1102FaultCode := INT(fbControl.uiFaultCode); iD1109HeartbeatEcho := INT(fbControl.uiHeartbeatEcho);
iD1110StartAck := INT(fbControl.uiStartAckSeq); iD1111StopAck := INT(fbControl.uiStopAckSeq);
iD1112SetZeroAck := INT(fbControl.uiSetZeroAckSeq); iD1113ResetFaultAck := INT(fbControl.uiResetFaultAckSeq);
iD1114PowerAck := INT(fbControl.uiPowerAckSeq); iD1115BufferAcked := INT(fbControl.uiBufferAckedSeq);
iD1116EventCount := INT(uiLoggerEventCount); iD1117Generation := INT(uiLoggerGeneration);
iD1118RunStatus := INT(fbControl.uiRunStatus); iD1200ProtocolVersion := INT(PROTOCOL_VERSION);
iD1201WordOrderHi := WORD_ORDER_PROBE_HIGH; iD1202WordOrderLo := WORD_ORDER_PROBE_LOW;
iD1206TimeSyncResponse := iD1203TimeSyncRequest;

diPositionMdeg := REAL_TO_DINT(rActualPosition * 1000.0);
diTargetMdeg := REAL_TO_DINT((rActualPosition + rMoveDistance) * 1000.0);
diVelocityMdegS := REAL_TO_DINT(rActualVelocity * 1000.0); diTickMs := DINT(udiPlcTickMs);
FC_SplitI32(diValue := diPositionMdeg, iHighWord => iD1103PositionHi, iLowWord => iD1104PositionLo);
FC_SplitI32(diValue := diTargetMdeg, iHighWord => iD1105TargetHi, iLowWord => iD1106TargetLo);
FC_SplitI32(diValue := diVelocityMdegS, iHighWord => iD1107VelocityHi, iLowWord => iD1108VelocityLo);
FC_SplitI32(diValue := diTickMs, iHighWord => iD1204TickHi, iLowWord => iD1205TickLo);
END_PROGRAM
