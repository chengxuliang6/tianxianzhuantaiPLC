(******************************************************************************
  AutoShop LiteST reference source -- create these two FCs before the FBs.
  DINT values are always explicitly represented as high-word then low-word INTs.
  Read and verify D0201:D0202 (0x1234/0x5678) on the actual PLC before any
  hardware write.  Do not rely on a native DINT variable-table binding order.
******************************************************************************)
FUNCTION FC_DecodeI32 : DINT
VAR_INPUT
    iHighWord : INT;
    iLowWord : INT;
END_VAR
VAR
    diLowWord : DINT;
END_VAR
diLowWord := INT_TO_DINT(iLowWord);
IF diLowWord < 0 THEN diLowWord := diLowWord + 65536; END_IF;
FC_DecodeI32 := INT_TO_DINT(iHighWord) * 65536 + diLowWord;
END_FUNCTION

FUNCTION FC_SplitI32 : BOOL
VAR_INPUT
    diValue : DINT;
END_VAR
VAR_OUTPUT
    iHighWord : INT;
    iLowWord : INT;
END_VAR
VAR
    diHighWord : DINT;
    diLowWord : DINT;
END_VAR
diHighWord := diValue / 65536;
diLowWord := diValue MOD 65536;
IF diLowWord < 0 THEN
    diLowWord := diLowWord + 65536;
    diHighWord := diHighWord - 1;
END_IF;
IF diHighWord >= 32768 THEN diHighWord := diHighWord - 65536; END_IF;
IF diLowWord >= 32768 THEN diLowWord := diLowWord - 65536; END_IF;
iHighWord := DINT_TO_INT(diHighWord);
iLowWord := DINT_TO_INT(diLowWord);
FC_SplitI32 := TRUE;
END_FUNCTION

FUNCTION FC_SplitU32 : BOOL
VAR_INPUT
    diValue : DINT;
END_VAR
VAR_OUTPUT
    iHighWord : INT;
    iLowWord : INT;
END_VAR
VAR
    diHighWord : DINT;
    diLowWord : DINT;
END_VAR
diHighWord := diValue / 65536;
diLowWord := diValue MOD 65536;
IF diLowWord < 0 THEN
    diLowWord := diLowWord + 65536;
    diHighWord := diHighWord - 1;
END_IF;
IF diHighWord >= 32768 THEN diHighWord := diHighWord - 65536; END_IF;
IF diLowWord >= 32768 THEN diLowWord := diLowWord - 65536; END_IF;
iHighWord := DINT_TO_INT(diHighWord);
iLowWord := DINT_TO_INT(diLowWord);
FC_SplitU32 := TRUE;
END_FUNCTION
