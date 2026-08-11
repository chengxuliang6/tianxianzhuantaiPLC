(******************************************************************************
  AutoShop LiteST reference -- create FB_TurntableControl then paste it.
  It is deterministic: a commanded move remains active until MC_Stop confirms
  standstill. The heartbeat is a communication watchdog, not safety-rated.
******************************************************************************)
FUNCTION_BLOCK FB_TurntableControl
VAR_INPUT
    uiMode : UINT; iDirection : INT; uiSpeedIndex : UINT;
    uiStartSeq : UINT; uiStopSeq : UINT; uiSetZeroSeq : UINT;
    uiResetFaultSeq : UINT; uiPowerSeq : UINT; uiHeartbeat : UINT;
    uiBufferAckSeq : UINT; udiNowTickMs : UDINT;
    diTotalRatioMilli : DINT; diRequestedAccelMilli : DINT;
    diRequestedDecelMilli : DINT; diRequestedStopDecelMilli : DINT;
    diBacklashMilli : DINT;
    rActualPositionDeg : REAL; rActualVelocityDegS : REAL;
    bPositionValid : BOOL; bVelocityValid : BOOL;
    bPositionReadError : BOOL; bVelocityReadError : BOOL;
    bPowerStatus : BOOL; bPowerError : BOOL; bMoveDone : BOOL;
    bMoveBusy : BOOL; bMoveAborted : BOOL; bMoveError : BOOL;
    bStopDone : BOOL; bStopBusy : BOOL; bStopError : BOOL;
    bSetPositionDone : BOOL; bSetPositionError : BOOL;
    bResetDone : BOOL; bResetBusy : BOOL; bResetError : BOOL;
    bLoggerBufferReady : BOOL; uiLoggerEventCount : UINT;
END_VAR
VAR_OUTPUT
    bPowerEnable : BOOL; bMoveRequest : BOOL; bStopRequest : BOOL;
    bSetZeroRequest : BOOL; bResetRequest : BOOL;
    bRunStarted : BOOL; bRunFinished : BOOL; bBufferAckEdge : BOOL;
    rMoveDistance : REAL; rSelectedVelocity : REAL;
    rAcceleration : REAL; rDeceleration : REAL; rStopDeceleration : REAL;
    rRunOriginDeg : REAL; iRunDirection : INT; udiRunStartTickMs : UDINT;
    rAcceptedTargetDeg : REAL;
    uiRunState : UINT; uiRunStatus : UINT; uiFaultCode : UINT;
    uiStatusFlags : UINT; uiHeartbeatEcho : UINT;
    uiStartAckSeq : UINT; uiStopAckSeq : UINT; uiSetZeroAckSeq : UINT;
    uiResetFaultAckSeq : UINT; uiPowerAckSeq : UINT; uiBufferAckedSeq : UINT;
    uiRetainedStopReason : UINT; bUnsafeStopFailure : BOOL;
END_VAR
VAR RETAIN
    bZeroValid : BOOL;
    uiRetainedStopReasonNv : UINT;
END_VAR
VAR
    uiLastHeartbeat : UINT; uiHeartbeatAgeScans : UINT;
    bActiveRun : BOOL; bStopLatched : BOOL; bFaultStopPending : BOOL;
    uiPendingFaultCode : UINT; bPowerCommandValue : BOOL;
    bResetPending : BOOL; uiResetPendingSeq : UINT;
    bStartAccepted : BOOL;
    rCandidateTargetDeg : REAL;
END_VAR

(* Defaults are one-scan rising-edge request pulses. *)
bMoveRequest := FALSE; bStopRequest := FALSE; bSetZeroRequest := FALSE;
bResetRequest := FALSE; bRunStarted := FALSE; bRunFinished := FALSE;
bBufferAckEdge := FALSE;

IF uiRunState = STATE_INITIALIZING THEN
    IF bZeroValid THEN uiRunState := STATE_READY; ELSE uiRunState := STATE_ZERO_REQUIRED; END_IF;
END_IF;

IF uiHeartbeat <> uiLastHeartbeat THEN
    uiLastHeartbeat := uiHeartbeat; uiHeartbeatAgeScans := 0; uiHeartbeatEcho := uiHeartbeat;
ELSIF uiHeartbeatAgeScans < 65535 THEN
    uiHeartbeatAgeScans := uiHeartbeatAgeScans + 1;
END_IF;

(* Terminal Stop response is handled before accepting another command. *)
IF bStopError AND bStopLatched THEN
    (* Unsafe terminal path: motion stop was not confirmed. Inhibit power and
       keep the event buffer unsealed for on-site assessment; do not auto-retry. *)
    bActiveRun := FALSE; bStopLatched := FALSE; bUnsafeStopFailure := TRUE;
    uiRunState := STATE_FAULT; uiRunStatus := RUN_FAULTED; uiFaultCode := FAULT_STOP_UNSAFE;
    uiRetainedStopReason := RUN_FAULTED; uiRetainedStopReasonNv := RUN_FAULTED;
ELSIF bStopDone AND bActiveRun THEN
    bActiveRun := FALSE; bStopLatched := FALSE; bRunFinished := TRUE;
    IF bFaultStopPending THEN
        bFaultStopPending := FALSE; uiRunState := STATE_FAULT; uiRunStatus := RUN_FAULTED;
        uiFaultCode := uiPendingFaultCode; rAcceptedTargetDeg := rActualPositionDeg;
    ELSE
        uiRunState := STATE_READY; uiRunStatus := uiRetainedStopReason;
        rAcceptedTargetDeg := rActualPositionDeg;
    END_IF;
END_IF;

(* RESET is an axis instruction result, not a display-only flag clear. *)
IF bResetPending AND (bResetDone OR bResetError) THEN
    uiResetFaultAckSeq := uiResetPendingSeq; bResetPending := FALSE;
    IF bResetDone THEN
        uiFaultCode := FAULT_NONE; uiRunStatus := RUN_IDLE;
        bFaultStopPending := FALSE; uiPendingFaultCode := FAULT_NONE;
        bStopLatched := FALSE; bUnsafeStopFailure := FALSE;
        uiRetainedStopReason := RUN_IDLE; uiRetainedStopReasonNv := RUN_IDLE;
        IF bZeroValid THEN uiRunState := STATE_READY; ELSE uiRunState := STATE_ZERO_REQUIRED; END_IF;
    ELSE
        uiRunState := STATE_FAULT; uiRunStatus := RUN_FAULTED; uiFaultCode := FAULT_RESET;
    END_IF;
END_IF;

(* STOP_SEQ is first and also consumes a same/earlier START_SEQ. *)
IF uiStopSeq <> uiStopAckSeq THEN
    uiStopAckSeq := uiStopSeq;
    uiStartAckSeq := uiStartSeq; (* discard pending start; stop has priority *)
    IF bActiveRun AND NOT bStopLatched THEN
        bStopLatched := TRUE; bStopRequest := TRUE; uiRunState := STATE_STOPPING;
        IF uiMode = MODE_AUTO THEN uiRetainedStopReason := RUN_AUTO_ABORTED;
        ELSE uiRetainedStopReason := RUN_MANUAL_STOPPED; END_IF;
        uiRetainedStopReasonNv := uiRetainedStopReason; uiRunStatus := uiRetainedStopReason;
    END_IF;
ELSIF bActiveRun AND NOT bStopLatched AND
      (bPositionReadError OR bVelocityReadError OR NOT bPositionValid OR NOT bVelocityValid
       OR bPowerError OR bMoveError OR bMoveAborted) THEN
    (* Preserve active motion and command MC_Stop in this scan; only Done seals it. *)
    bStopLatched := TRUE; bFaultStopPending := TRUE; bStopRequest := TRUE;
    uiRunState := STATE_STOPPING; uiRunStatus := RUN_FAULTED;
    uiRetainedStopReason := RUN_FAULTED; uiRetainedStopReasonNv := RUN_FAULTED;
    IF bPositionReadError OR NOT bPositionValid THEN uiPendingFaultCode := FAULT_POSITION_READ;
    ELSIF bVelocityReadError OR NOT bVelocityValid THEN uiPendingFaultCode := FAULT_VELOCITY_READ;
    ELSE uiPendingFaultCode := FAULT_MOTION; END_IF;
ELSIF bActiveRun AND NOT bStopLatched AND (uiHeartbeatAgeScans > 1000) THEN
    bStopLatched := TRUE; bStopRequest := TRUE; uiRunState := STATE_STOPPING;
    uiRetainedStopReason := RUN_COMMUNICATION_ABORTED;
    uiRetainedStopReasonNv := uiRetainedStopReason; uiRunStatus := RUN_COMMUNICATION_ABORTED;
    uiFaultCode := FAULT_HEARTBEAT;
ELSIF uiResetFaultSeq <> uiResetFaultAckSeq AND NOT bResetPending THEN
    IF bActiveRun THEN
        uiResetFaultAckSeq := uiResetFaultSeq; uiFaultCode := FAULT_INVALID_COMMAND;
    ELSE
        bResetPending := TRUE; uiResetPendingSeq := uiResetFaultSeq; bResetRequest := TRUE;
    END_IF;
ELSIF uiBufferAckSeq <> uiBufferAckedSeq THEN
    IF NOT bActiveRun THEN uiBufferAckedSeq := uiBufferAckSeq; bBufferAckEdge := TRUE; END_IF;
ELSIF uiPowerSeq <> uiPowerAckSeq THEN
    uiPowerAckSeq := uiPowerSeq;
    IF NOT bActiveRun THEN bPowerCommandValue := NOT bPowerCommandValue; END_IF;
ELSIF uiSetZeroSeq <> uiSetZeroAckSeq THEN
    IF NOT bActiveRun AND NOT bMoveBusy AND NOT bStopBusy AND NOT bPowerError
       AND bPositionValid AND ABS(rActualVelocityDegS) < 0.001 THEN
        uiSetZeroAckSeq := uiSetZeroSeq; bSetZeroRequest := TRUE;
    ELSE
        uiSetZeroAckSeq := uiSetZeroSeq; uiFaultCode := FAULT_INVALID_COMMAND;
    END_IF;
ELSIF uiStartSeq <> uiStartAckSeq THEN
    (* Every START_SEQ receives a final accept or reject acknowledgement. *)
    uiStartAckSeq := uiStartSeq; bStartAccepted := TRUE;
    IF bActiveRun OR bLoggerBufferReady OR NOT bZeroValid OR NOT bPowerStatus
       OR NOT bPositionValid OR NOT bVelocityValid OR bPowerError OR bMoveError
       OR bUnsafeStopFailure OR (uiHeartbeatAgeScans > 1000) THEN bStartAccepted := FALSE; END_IF;
    IF (diRequestedAccelMilli <= 0) OR (diRequestedDecelMilli <= 0)
       OR (diRequestedStopDecelMilli <= 0) OR (diBacklashMilli <> 0)
       OR (diTotalRatioMilli <> TOTAL_RATIO_MILLI) THEN bStartAccepted := FALSE; END_IF;
    CASE uiSpeedIndex OF
        1: rSelectedVelocity := 1.0; 2: rSelectedVelocity := 2.0;
        3: rSelectedVelocity := 4.0; 4: rSelectedVelocity := 5.0;
        5: rSelectedVelocity := 10.0;
    ELSE bStartAccepted := FALSE; rSelectedVelocity := 0.0;
    END_CASE;
    IF (iDirection <> DIRECTION_CW) AND (iDirection <> DIRECTION_CCW) THEN bStartAccepted := FALSE; END_IF;
    IF uiMode = MODE_AUTO THEN
        rCandidateTargetDeg := rActualPositionDeg + 360.0 * REAL(iDirection);
        IF (rCandidateTargetDeg < -360.0) OR (rCandidateTargetDeg > 360.0) THEN bStartAccepted := FALSE; END_IF;
    ELSIF uiMode = MODE_MANUAL THEN
        rCandidateTargetDeg := rActualPositionDeg + 360.0 * REAL(iDirection);
        IF rCandidateTargetDeg > 360.0 THEN rCandidateTargetDeg := 360.0; END_IF;
        IF rCandidateTargetDeg < -360.0 THEN rCandidateTargetDeg := -360.0; END_IF;
    ELSE bStartAccepted := FALSE;
    END_IF;
    rMoveDistance := rCandidateTargetDeg - rActualPositionDeg;
    IF ABS(rMoveDistance) < 0.001 THEN bStartAccepted := FALSE; END_IF;
    IF bStartAccepted THEN
        (* Parameters are validated and latched only at accepted-run boundary. *)
        uiFaultCode := FAULT_NONE; bFaultStopPending := FALSE;
        uiPendingFaultCode := FAULT_NONE; bStopLatched := FALSE;
        uiRetainedStopReason := RUN_IDLE; uiRetainedStopReasonNv := RUN_IDLE;
        rAcceleration := DINT_TO_REAL(diRequestedAccelMilli) / 1000.0;
        rDeceleration := DINT_TO_REAL(diRequestedDecelMilli) / 1000.0;
        rStopDeceleration := DINT_TO_REAL(diRequestedStopDecelMilli) / 1000.0;
        rAcceptedTargetDeg := rCandidateTargetDeg;
        bMoveRequest := TRUE; bRunStarted := TRUE; bActiveRun := TRUE; bStopLatched := FALSE;
        rRunOriginDeg := rActualPositionDeg; iRunDirection := iDirection; udiRunStartTickMs := udiNowTickMs;
        uiRunStatus := RUN_RUNNING;
        IF uiMode = MODE_AUTO THEN uiRunState := STATE_AUTO_RUNNING; ELSE uiRunState := STATE_MANUAL_RUNNING; END_IF;
    ELSE
        uiFaultCode := FAULT_START_REJECTED; uiRunStatus := RUN_FAULTED;
        IF NOT bZeroValid THEN uiRunState := STATE_ZERO_REQUIRED; ELSE uiRunState := STATE_FAULT; END_IF;
    END_IF;
END_IF;

IF bSetPositionDone THEN bZeroValid := TRUE; IF NOT bActiveRun THEN uiRunState := STATE_READY; END_IF; END_IF;
IF bSetPositionError THEN uiRunState := STATE_FAULT; uiRunStatus := RUN_FAULTED; uiFaultCode := FAULT_MOTION; END_IF;
IF bMoveDone AND bActiveRun AND NOT bStopLatched THEN
    bActiveRun := FALSE; bRunFinished := TRUE; uiRunState := STATE_READY; uiRunStatus := RUN_COMPLETED;
END_IF;

bPowerEnable := bPowerCommandValue AND NOT bUnsafeStopFailure;
uiStatusFlags := 0;
IF bZeroValid THEN uiStatusFlags := uiStatusFlags OR STATUS_ZERO_VALID; END_IF;
IF bPowerStatus THEN uiStatusFlags := uiStatusFlags OR STATUS_POWERED; END_IF;
IF bLoggerBufferReady THEN uiStatusFlags := uiStatusFlags OR STATUS_BUFFER_READY; END_IF;
IF uiHeartbeatAgeScans <= 1000 THEN uiStatusFlags := uiStatusFlags OR STATUS_HEARTBEAT_OK; END_IF;
IF (rActualPositionDeg <= -360.0) OR (rActualPositionDeg >= 360.0) THEN uiStatusFlags := uiStatusFlags OR STATUS_SOFT_LIMIT; END_IF;
END_FUNCTION_BLOCK
