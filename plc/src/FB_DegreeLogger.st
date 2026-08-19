(******************************************************************************
  AutoShop LiteST reference source -- create FB_DegreeLogger, paste declaration
  and body.  aEventWords is the INT[2160] variable-table array bound at D2000.
******************************************************************************)
FUNCTION_BLOCK FB_DegreeLogger
VAR_INPUT
    rPreviousPositionDeg : REAL;
    rCurrentPositionDeg : REAL;
    rRunOriginDeg : REAL;
    iDirection : INT;
    udiRunStartTickMs : DINT;
    udiNowTickMs : DINT;
    bRunStarted : BOOL;
    bRunFinished : BOOL;
    bBufferAckEdge : BOOL;
END_VAR
VAR_IN_OUT
    aEventWords : ARRAY[0..2159] OF INT;
END_VAR
VAR_OUTPUT
    bBufferReady : BOOL;
    uiEventCount : INT;
    uiGeneration : INT;
END_VAR
VAR
    rPreviousTravel : REAL;
    rCurrentTravel : REAL;
    rCrossingPosition : REAL;
    iFirstAngle : INT;
    iLastAngle : INT;
    iCrossing : INT;
    iTravelAngle : INT;
    iBase : INT;
    diPositionMdeg : DINT;
    udiElapsedMs : DINT;
    diElapsedBeforeWrap, diElapsedAfterWrap : DINT;
    iPositionHigh : INT;
    iPositionLow : INT;
    iElapsedHigh : INT;
    iElapsedLow : INT;
END_VAR

(* An acknowledged completed buffer is released before a possible run start. *)
IF bBufferAckEdge AND bBufferReady THEN
    bBufferReady := FALSE;
    uiEventCount := 0;
END_IF;

IF bRunStarted AND NOT bBufferReady THEN
    uiEventCount := 0;
    IF uiGeneration = 32767 THEN
        uiGeneration := -32768;
    ELSE
        uiGeneration := uiGeneration + 1;
    END_IF;
END_IF;

(* A sealed buffer is immutable. A controller prevents another run until ACK. *)
IF NOT bBufferReady AND NOT bRunStarted THEN
    rPreviousTravel := (rPreviousPositionDeg - rRunOriginDeg) * REAL(iDirection);
    rCurrentTravel := (rCurrentPositionDeg - rRunOriginDeg) * REAL(iDirection);
    iFirstAngle := MAX(1, REAL_TO_INT(FLOOR(rPreviousTravel)) + 1);
    iLastAngle := MIN(360, REAL_TO_INT(FLOOR(rCurrentTravel)));

    (* Fixed guard: at most 360 legal travel-angle writes per scan. *)
    FOR iCrossing := 1 TO 360 DO
        iTravelAngle := iFirstAngle + iCrossing - 1;
        IF iTravelAngle > iLastAngle THEN
            EXIT;
        END_IF;
        IF uiEventCount < 360 THEN
            iBase := 6 * uiEventCount;
            rCrossingPosition := rRunOriginDeg + REAL(iDirection) * REAL(iTravelAngle);
            diPositionMdeg := REAL_TO_DINT(rCrossingPosition * 1000.0);
            (* Calculate the raw u32 duration without a signed DINT overflow. *)
            IF (udiNowTickMs < 0) AND (udiRunStartTickMs >= 0) THEN
                (* The raw tick passed from the positive signed half to the negative half. *)
                diElapsedBeforeWrap := 2147483647 - udiRunStartTickMs;
                diElapsedAfterWrap := udiNowTickMs - (-2147483648);
                IF diElapsedBeforeWrap < 2147483647 - diElapsedAfterWrap THEN
                    udiElapsedMs := diElapsedBeforeWrap + diElapsedAfterWrap + 1;
                ELSE
                    udiElapsedMs := -2147483648 +
                        (diElapsedBeforeWrap - (2147483647 - diElapsedAfterWrap));
                END_IF;
            ELSIF (udiNowTickMs >= 0) AND (udiRunStartTickMs < 0) THEN
                (* The raw tick crossed its u32 boundary while the run was active. *)
                IF udiNowTickMs <= 2147483647 + udiRunStartTickMs THEN
                    udiElapsedMs := udiNowTickMs - udiRunStartTickMs;
                ELSE
                    udiElapsedMs := -2147483648 +
                        (udiNowTickMs - (2147483647 + udiRunStartTickMs));
                END_IF;
            ELSE
                udiElapsedMs := udiNowTickMs - udiRunStartTickMs;
            END_IF;
            FC_SplitI32(diValue := diPositionMdeg, iHighWord => iPositionHigh, iLowWord => iPositionLow);
            FC_SplitU32(diValue := udiElapsedMs, iHighWord => iElapsedHigh, iLowWord => iElapsedLow);
            aEventWords[iBase + 0] := INT(uiEventCount + 1);
            aEventWords[iBase + 1] := iTravelAngle;
            aEventWords[iBase + 2] := iPositionHigh;
            aEventWords[iBase + 3] := iPositionLow;
            aEventWords[iBase + 4] := iElapsedHigh;
            aEventWords[iBase + 5] := iElapsedLow;
            uiEventCount := uiEventCount + 1;
        ELSE
            EXIT;
        END_IF;
    END_FOR;
END_IF;

IF bRunFinished THEN
    bBufferReady := TRUE;
END_IF;
END_FUNCTION_BLOCK
