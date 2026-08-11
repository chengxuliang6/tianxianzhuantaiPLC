(******************************************************************************
  AutoShop LiteST reference source -- create FB_TurntableControl, paste this
  declaration/body, then instantiate it from PRG_MAIN. Sequence values are
  16-bit bit patterns: inequality is wrap-safe because only change matters.
  The heartbeat is a communication watchdog, not a safety-rated watchdog.
******************************************************************************)
FUNCTION_BLOCK FB_TurntableControl
VAR_INPUT
    uiMode : UINT; iDirection : INT; uiSpeedIndex : UINT;
    uiStartSeq : UINT; uiStopSeq : UINT; uiSetZeroSeq : UINT;
    uiResetFaultSeq : UINT; uiPowerSeq : UINT; uiHeartbeat : UINT;
    uiBufferAckSeq : UINT;
    udiNowTickMs : UDINT;
    rActualPositionDeg : REAL; rActualVelocityDegS : REAL;
    bPositionValid : BOOL; bVelocityValid : BOOL;
    bPositionReadError : BOOL; bVelocityReadError : BOOL;
    bPowerStatus : BOOL; bPowerError : BOOL; bMoveDone : BOOL;
    bMoveBusy : BOOL; bMoveAborted : BOOL; bMoveError : BOOL;
    bStopDone : BOOL; bStopBusy : BOOL; bStopError : BOOL;
    bSetPositionDone : BOOL; bSetPositionError : BOOL;
    bLoggerBufferReady : BOOL; uiLoggerEventCount : UINT;
END_VAR
VAR_OUTPUT
    bPowerEnable : BOOL; bMoveRequest : BOOL; bStopRequest : BOOL;
    bSetZeroRequest : BOOL; bRunStarted : BOOL; bRunFinished : BOOL;
    bBufferAckEdge : BOOL;
    rMoveDistance : REAL; rSelectedVelocity : REAL;
    rAcceleration : REAL; rDeceleration : REAL; rStopDeceleration : REAL;
    rRunOriginDeg : REAL; iRunDirection : INT;
    udiRunStartTickMs : UDINT;
    uiRunState : UINT; uiRunStatus : UINT; uiFaultCode : UINT;
    uiStatusFlags : UINT; uiHeartbeatEcho : UINT;
    uiStartAckSeq : UINT; uiStopAckSeq : UINT; uiSetZeroAckSeq : UINT;
    uiResetFaultAckSeq : UINT; uiPowerAckSeq : UINT; uiBufferAckedSeq : UINT;
    uiRetainedStopReason : UINT;
END_VAR
VAR RETAIN
    bZeroValid : BOOL;
    uiRetainedStopReasonNv : UINT;
END_VAR
VAR
    uiLastHeartbeat : UINT;
    uiHeartbeatAgeScans : UINT;
    rTargetPositionDeg : REAL;
    bActiveRun : BOOL;
    bStopLatched : BOOL;
    bPowerCommandValue : BOOL;
END_VAR

(* Defaults make edge outputs exactly one scan wide. *)
bMoveRequest := FALSE; bStopRequest := FALSE; bSetZeroRequest := FALSE;
bRunStarted := FALSE; bRunFinished := FALSE; bBufferAckEdge := FALSE;
rAcceleration := 5.0; rDeceleration := 5.0; rStopDeceleration := 10.0;

(* Heartbeat comparison uses raw UINT bit patterns; timeout is strictly >1000. *)
IF uiHeartbeat <> uiLastHeartbeat THEN
    uiLastHeartbeat := uiHeartbeat;
    uiHeartbeatAgeScans := 0;
    uiHeartbeatEcho := uiHeartbeat;
ELSIF uiHeartbeatAgeScans < 65535 THEN
    uiHeartbeatAgeScans := uiHeartbeatAgeScans + 1;
END_IF;

(* STOP_SEQ priority is intentionally above START_SEQ and all other commands. *)
IF uiStopSeq <> uiStopAckSeq THEN
    uiStopAckSeq := uiStopSeq;
    IF bActiveRun AND NOT bStopLatched THEN
        bStopLatched := TRUE;
        bStopRequest := TRUE;
        uiRunState := STATE_STOPPING;
        IF uiMode = MODE_AUTO THEN uiRetainedStopReason := RUN_AUTO_ABORTED;
        ELSE uiRetainedStopReason := RUN_MANUAL_STOPPED; END_IF;
        uiRetainedStopReasonNv := uiRetainedStopReason;
        uiRunStatus := uiRetainedStopReason;
    END_IF;
ELSIF bActiveRun AND (uiHeartbeatAgeScans > 1000) THEN
    IF NOT bStopLatched THEN
        bStopLatched := TRUE;
        bStopRequest := TRUE;
        uiRunState := STATE_STOPPING;
        uiRetainedStopReason := RUN_COMMUNICATION_ABORTED;
        uiRetainedStopReasonNv := uiRetainedStopReason;
        uiRunStatus := RUN_COMMUNICATION_ABORTED;
        uiFaultCode := FAULT_HEARTBEAT;
    END_IF;
ELSIF uiBufferAckSeq <> uiBufferAckedSeq THEN
    (* Release only after the PC reports durable event storage. *)
    IF NOT bActiveRun THEN
        uiBufferAckedSeq := uiBufferAckSeq;
        bBufferAckEdge := TRUE;
    END_IF;
ELSIF uiPowerSeq <> uiPowerAckSeq THEN
    uiPowerAckSeq := uiPowerSeq;
    IF NOT bActiveRun THEN bPowerCommandValue := NOT bPowerCommandValue; END_IF;
ELSIF uiResetFaultSeq <> uiResetFaultAckSeq THEN
    IF NOT bActiveRun AND NOT bPowerError AND NOT bMoveError THEN
        uiResetFaultAckSeq := uiResetFaultSeq;
        uiFaultCode := FAULT_NONE;
        IF bZeroValid THEN uiRunState := STATE_READY; ELSE uiRunState := STATE_ZERO_REQUIRED; END_IF;
    END_IF;
ELSIF uiSetZeroSeq <> uiSetZeroAckSeq THEN
    (* Manual set-zero is accepted only while safely stopped and fault-free. *)
    IF NOT bActiveRun AND NOT bMoveBusy AND NOT bStopBusy AND NOT bPowerError
       AND bPositionValid AND ABS(rActualVelocityDegS) < 0.001 THEN
        uiSetZeroAckSeq := uiSetZeroSeq;
        bSetZeroRequest := TRUE;
    END_IF;
ELSIF uiStartSeq <> uiStartAckSeq THEN
    (* START_SEQ is deliberately last: stop remains higher priority. *)
    IF NOT bActiveRun AND NOT bLoggerBufferReady AND bZeroValid AND bPowerStatus
       AND bPositionValid AND bVelocityValid AND NOT bPowerError AND NOT bMoveError THEN
        CASE uiSpeedIndex OF
            1: rSelectedVelocity := 1.0; 2: rSelectedVelocity := 2.0;
            3: rSelectedVelocity := 4.0; 4: rSelectedVelocity := 5.0;
            5: rSelectedVelocity := 10.0;
        ELSE
            rSelectedVelocity := 0.0; uiFaultCode := FAULT_INVALID_COMMAND;
        END_CASE;
        IF (iDirection = DIRECTION_CW) OR (iDirection = DIRECTION_CCW) THEN
            IF uiMode = MODE_AUTO THEN
                rTargetPositionDeg := rActualPositionDeg + 360.0 * REAL(iDirection);
                IF (rTargetPositionDeg >= -360.0) AND (rTargetPositionDeg <= 360.0) THEN
                    rMoveDistance := 360.0 * REAL(iDirection);
                ELSE
                    rSelectedVelocity := 0.0; uiFaultCode := FAULT_AUTO_LIMIT;
                END_IF;
            ELSIF uiMode = MODE_MANUAL THEN
                rTargetPositionDeg := rActualPositionDeg + 360.0 * REAL(iDirection);
                IF rTargetPositionDeg > 360.0 THEN rTargetPositionDeg := 360.0; END_IF;
                IF rTargetPositionDeg < -360.0 THEN rTargetPositionDeg := -360.0; END_IF;
                rMoveDistance := rTargetPositionDeg - rActualPositionDeg;
            ELSE
                rSelectedVelocity := 0.0; uiFaultCode := FAULT_INVALID_COMMAND;
            END_IF;
        ELSE
            rSelectedVelocity := 0.0; uiFaultCode := FAULT_INVALID_COMMAND;
        END_IF;
        IF rSelectedVelocity > 0.0 AND ABS(rMoveDistance) > 0.0 THEN
            uiStartAckSeq := uiStartSeq;
            bMoveRequest := TRUE; bRunStarted := TRUE; bActiveRun := TRUE;
            bStopLatched := FALSE; rRunOriginDeg := rActualPositionDeg; iRunDirection := iDirection;
            udiRunStartTickMs := udiNowTickMs;
            uiRunStatus := RUN_RUNNING;
            IF uiMode = MODE_AUTO THEN uiRunState := STATE_AUTO_RUNNING;
            ELSE uiRunState := STATE_MANUAL_RUNNING; END_IF;
        END_IF;
    END_IF;
END_IF;

IF bSetPositionDone THEN
    bZeroValid := TRUE;
    IF NOT bActiveRun THEN uiRunState := STATE_READY; END_IF;
END_IF;

IF bMoveDone AND bActiveRun AND NOT bStopLatched THEN
    bActiveRun := FALSE; bRunFinished := TRUE; uiRunState := STATE_READY; uiRunStatus := RUN_COMPLETED;
END_IF;
IF bStopDone AND bActiveRun THEN
    bActiveRun := FALSE; bRunFinished := TRUE; uiRunState := STATE_READY;
    uiRunStatus := uiRetainedStopReason;
END_IF;
IF bPositionReadError OR bVelocityReadError THEN
    bActiveRun := FALSE; bRunFinished := TRUE; uiRunState := STATE_FAULT;
    uiRunStatus := RUN_FAULTED;
    IF bPositionReadError THEN uiFaultCode := FAULT_POSITION_READ;
    ELSE uiFaultCode := FAULT_VELOCITY_READ; END_IF;
    uiRetainedStopReason := RUN_FAULTED; uiRetainedStopReasonNv := RUN_FAULTED;
ELSIF bPowerError OR bMoveError OR bStopError OR bSetPositionError
   OR (bMoveAborted AND NOT bStopLatched) THEN
    bActiveRun := FALSE; bRunFinished := TRUE; uiRunState := STATE_FAULT;
    uiRunStatus := RUN_FAULTED; uiFaultCode := FAULT_MOTION;
    uiRetainedStopReason := RUN_FAULTED; uiRetainedStopReasonNv := RUN_FAULTED;
END_IF;

bPowerEnable := bPowerCommandValue;
IF (NOT bActiveRun) AND (uiRunState = STATE_INITIALIZING) THEN
    IF bZeroValid THEN uiRunState := STATE_READY; ELSE uiRunState := STATE_ZERO_REQUIRED; END_IF;
END_IF;
uiStatusFlags := 0;
IF bZeroValid THEN uiStatusFlags := uiStatusFlags OR STATUS_ZERO_VALID; END_IF;
IF bPowerStatus THEN uiStatusFlags := uiStatusFlags OR STATUS_POWERED; END_IF;
IF bLoggerBufferReady THEN uiStatusFlags := uiStatusFlags OR STATUS_BUFFER_READY; END_IF;
IF uiHeartbeatAgeScans <= 1000 THEN uiStatusFlags := uiStatusFlags OR STATUS_HEARTBEAT_OK; END_IF;
IF (rActualPositionDeg <= -360.0) OR (rActualPositionDeg >= 360.0) THEN
    uiStatusFlags := uiStatusFlags OR STATUS_SOFT_LIMIT;
END_IF;
END_FUNCTION_BLOCK
