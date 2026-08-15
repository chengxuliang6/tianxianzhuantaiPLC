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
(* iHighWord is signed; UINT(iLowWord) preserves its 16 wire bits. *)
FC_DecodeI32 := DINT(iHighWord) * 65536 + DINT(UINT(iLowWord));
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
    udiBits : UDINT;
END_VAR
udiBits := UDINT(diValue);
iHighWord := INT(SHR(udiBits, 16));
iLowWord := INT(udiBits AND 16#0000FFFF);
FC_SplitI32 := TRUE;
END_FUNCTION

FUNCTION FC_SplitU32 : BOOL
VAR_INPUT
    udiValue : UDINT;
END_VAR
VAR_OUTPUT
    iHighWord : INT;
    iLowWord : INT;
END_VAR
iHighWord := INT(SHR(udiValue, 16));
iLowWord := INT(udiValue AND 16#0000FFFF);
FC_SplitU32 := TRUE;
END_FUNCTION
