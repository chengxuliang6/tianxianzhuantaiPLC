(******************************************************************************
  AutoShop LiteST reference source -- create program POU PRG_MAIN and paste the
  declaration/body. Create Axis_0 in AutoShop's axis configuration, not here.
  Create the MODBUS_D variable table described in plc/README.md before binding.
******************************************************************************)
PROGRAM PRG_MAIN
VAR
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
    bResetExecute, bResetDone, bResetBusy, bResetError : BOOL;
    iResetErrorID : INT;
    bPositionValid, bPositionBusy, bPositionError : BOOL;
    iPositionErrorID : INT;
    bVelocityValid, bVelocityBusy, bVelocityError : BOOL;
    iVelocityErrorID : INT;
    rPreviousPosition : REAL;
    rLoggerPosition : REAL;
    bLoggerStarted, bLoggerFinished, bLoggerAck : BOOL;
    bLoggerBufferReady : BOOL;
    uiLoggerEventCount, uiLoggerGeneration : UINT;
    diPositionMdeg, diTargetMdeg, diVelocityMdegS : DINT;
    diTotalRatioMilli, diRequestedAccelMilli, diRequestedDecelMilli : DINT;
    diRequestedStopDecelMilli, diBacklashMilli : DINT;
    fbControl : FB_TurntableControl;
    fbLogger : FB_DegreeLogger;
END_VAR

(* PRG_MAIN must be scheduled as a constant 1 ms task; tick is not retained. *)
udiPlcTickMs := udiPlcTickMs + 1;

(* Decode all runtime parameters explicitly. D1010 ratio must match the
   offline Axis_0 scaling; phase 1 does not perform backlash compensation. *)
diTotalRatioMilli := FC_DecodeI32(iHighWord := iD1010RatioHi, iLowWord := iD1011RatioLo);
diRequestedAccelMilli := FC_DecodeI32(iHighWord := iD1012AccelHi, iLowWord := iD1013AccelLo);
diRequestedDecelMilli := FC_DecodeI32(iHighWord := iD1014DecelHi, iLowWord := iD1015DecelLo);
diRequestedStopDecelMilli := FC_DecodeI32(iHighWord := iD1016StopDecelHi, iLowWord := iD1017StopDecelLo);
diBacklashMilli := FC_DecodeI32(iHighWord := iD1018BacklashHi, iLowWord := iD1019BacklashLo);

(* 1. Refresh feedback first. These enables remain TRUE for continuously valid reads. *)
MC_ReadActualPosition(Enable := TRUE, Axis := Axis_0,
    Valid => bPositionValid, Busy => bPositionBusy,
    Position => rActualPosition,
    Error => bPositionError, ErrorID => iPositionErrorID);

MC_ReadActualVelocity(Enable := TRUE, Axis := Axis_0,
    Valid => bVelocityValid, Busy => bVelocityBusy,
    Velocity => rActualVelocity,
    Error => bVelocityError, ErrorID => iVelocityErrorID);

(* Invalid position feedback must not create synthetic degree crossings. *)
IF bPositionValid AND NOT bPositionError THEN
    rLoggerPosition := rActualPosition;
END_IF;

(* 2. Deterministic command/state decision uses current feedback and raw INT wire words. *)
fbControl(
    uiMode := UINT(iD1000Mode), iDirection := iD1001Direction,
    uiSpeedIndex := UINT(iD1002SpeedIndex), uiStartSeq := UINT(iD1003StartSeq),
    uiStopSeq := UINT(iD1004StopSeq), uiSetZeroSeq := UINT(iD1005SetZeroSeq),
    uiResetFaultSeq := UINT(iD1006ResetFaultSeq), uiPowerSeq := UINT(iD1007PowerSeq),
    uiHeartbeat := UINT(iD1008Heartbeat), uiBufferAckSeq := UINT(iD1009BufferAckSeq),
    udiNowTickMs := udiPlcTickMs,
    diTotalRatioMilli := diTotalRatioMilli, diRequestedAccelMilli := diRequestedAccelMilli,
    diRequestedDecelMilli := diRequestedDecelMilli, diRequestedStopDecelMilli := diRequestedStopDecelMilli,
    diBacklashMilli := diBacklashMilli,
    rActualPositionDeg := rActualPosition, rActualVelocityDegS := rActualVelocity,
    bPositionValid := bPositionValid, bVelocityValid := bVelocityValid,
    bPositionReadError := bPositionError, bVelocityReadError := bVelocityError,
    bPowerStatus := bPowerStatus, bPowerError := bPowerError, bMoveDone := bMoveDone,
    bMoveBusy := bMoveBusy, bMoveAborted := bMoveAborted, bMoveError := bMoveError,
    bStopDone := bStopDone, bStopBusy := bStopBusy, bStopError := bStopError,
    bSetPositionDone := bSetPositionDone, bSetPositionError := bSetPositionError,
    bResetDone := bResetDone, bResetBusy := bResetBusy, bResetError := bResetError,
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

bResetExecute := fbControl.bResetRequest;
MC_Reset(Execute := bResetExecute, Axis := Axis_0,
    Done => bResetDone, Busy => bResetBusy,
    Error => bResetError, ErrorID => iResetErrorID);
bResetExecute := FALSE;

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
IF bStopDone OR bStopError THEN
    bStopExecute := FALSE;
END_IF;

MC_Power(Enable := bPowerEnable, Axis := Axis_0,
    Status => bPowerStatus, Busy => bPowerBusy,
    Error => bPowerError, ErrorID => iPowerErrorID);

(* 4. Capture all crossed travel integers, then publish a stable register snapshot. *)
bLoggerStarted := fbControl.bRunStarted; bLoggerFinished := fbControl.bRunFinished;
bLoggerAck := fbControl.bBufferAckEdge;
fbLogger(rPreviousPositionDeg := rPreviousPosition, rCurrentPositionDeg := rLoggerPosition,
    rRunOriginDeg := fbControl.rRunOriginDeg, iDirection := fbControl.iRunDirection,
    udiRunStartTickMs := fbControl.udiRunStartTickMs, udiNowTickMs := udiPlcTickMs,
    bRunStarted := bLoggerStarted, bRunFinished := bLoggerFinished, bBufferAckEdge := bLoggerAck,
    aEventWords := aD2000Events, bBufferReady => bLoggerBufferReady,
    uiEventCount => uiLoggerEventCount, uiGeneration => uiLoggerGeneration);
IF bPositionValid AND NOT bPositionError THEN
    rPreviousPosition := rLoggerPosition;
END_IF;

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
diTargetMdeg := REAL_TO_DINT(fbControl.rAcceptedTargetDeg * 1000.0);
diVelocityMdegS := REAL_TO_DINT(rActualVelocity * 1000.0);
FC_SplitI32(diValue := diPositionMdeg, iHighWord => iD1103PositionHi, iLowWord => iD1104PositionLo);
FC_SplitI32(diValue := diTargetMdeg, iHighWord => iD1105TargetHi, iLowWord => iD1106TargetLo);
FC_SplitI32(diValue := diVelocityMdegS, iHighWord => iD1107VelocityHi, iLowWord => iD1108VelocityLo);
FC_SplitU32(udiValue := fbControl.udiRunStartTickMs, iHighWord => iD1119RunStartTickHi, iLowWord => iD1120RunStartTickLo);
FC_SplitU32(udiValue := udiPlcTickMs, iHighWord => iD1204TickHi, iLowWord => iD1205TickLo);
END_PROGRAM
