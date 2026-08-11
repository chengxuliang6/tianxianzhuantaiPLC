"""Static contract for the AutoShop LiteST reference source set.

The files are intentionally inspected as text: the installed AutoShop compiler,
not pytest, remains the final authority for target-specific LiteST syntax.
"""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
PLC = ROOT / "plc"
SRC = PLC / "src"


def source(name: str) -> str:
    return (SRC / name).read_text(encoding="utf-8")


def test_main_has_all_manual_confirmed_plcopen_calls_and_pins() -> None:
    main = source("PRG_MAIN.st")
    required = {
        "MC_Power": ("Enable := bPowerEnable", "Axis := Axis_0", "Status => bPowerStatus"),
        "MC_SetPosition": ("Execute := bSetPositionExecute", "Position := 0.0", "Mode := 0"),
        "MC_MoveRelative": ("Execute := bMoveExecute", "Distance := rMoveDistance", "CurveType := 0"),
        "MC_Stop": ("Execute := bStopExecute", "Deceleration := rStopDeceleration", "Done => bStopDone"),
        "MC_ReadActualPosition": ("Enable := TRUE", "Position => rActualPosition", "Valid => bPositionValid"),
        "MC_ReadActualVelocity": ("Enable := TRUE", "Velocity => rActualVelocity", "Valid => bVelocityValid"),
    }
    for instruction, pins in required.items():
        assert instruction in main
        for pin in pins:
            assert pin in main


def test_stop_done_releases_execute_and_has_priority_over_start() -> None:
    main = source("PRG_MAIN.st")
    controller = source("FB_TurntableControl.st")
    assert re.search(r"IF\s+bStopDone\s+OR\s+bStopError\s+THEN\s*\n\s*bStopExecute\s*:=\s*FALSE", main)
    assert controller.index("STOP_SEQ") < controller.index("START_SEQ")


def test_stop_discards_pending_start_and_read_fault_stops_before_fault_seal() -> None:
    controller = source("FB_TurntableControl.st")
    stop_branch = controller[controller.index("IF uiStopSeq <> uiStopAckSeq"):controller.index("ELSIF bActiveRun")]
    assert "uiStartAckSeq := uiStartSeq" in stop_branch
    assert "bPositionReadError" in controller and "bVelocityReadError" in controller
    assert "bStopRequest := TRUE" in controller
    assert "bStopDone AND bActiveRun" in controller
    assert "IF bStopError AND bStopLatched" in controller
    assert "bUnsafeStopFailure := TRUE" in controller


def test_main_has_real_axis_reset_and_latched_accepted_target_publication() -> None:
    main = source("PRG_MAIN.st")
    controller = source("FB_TurntableControl.st")
    assert "MC_Reset(Execute := bResetExecute, Axis := Axis_0" in main
    assert "Done => bResetDone" in main and "Error => bResetError" in main
    assert "rAcceptedTargetDeg" in controller
    assert "rCandidateTargetDeg" in controller
    assert "rAcceptedTargetDeg := rCandidateTargetDeg" in controller
    assert "IF bResetPending AND (bResetDone OR bResetError)" in controller
    assert "uiResetFaultAckSeq := uiResetPendingSeq" in controller
    assert "rAcceptedTargetDeg * 1000.0" in main
    assert "rActualPosition + rMoveDistance" not in main


def test_logger_keeps_all_crossings_and_encodes_elapsed_as_u32() -> None:
    logger = source("FB_DegreeLogger.st")
    codec = source("Turntable_RegisterCodec.st")
    assert "FOR iCrossing := 1 TO 360" in logger
    assert "udiElapsedMs : UDINT" in logger
    assert "FC_SplitU32" in logger and "FC_SplitU32" in codec


def test_start_has_a_terminal_ack_freshness_and_latched_parameter_path() -> None:
    main = source("PRG_MAIN.st")
    controller = source("FB_TurntableControl.st")
    assert "uiStartAckSeq := uiStartSeq; bStartAccepted := TRUE" in controller
    assert "(uiHeartbeatAgeScans > 1000)" in controller
    assert "OR bUnsafeStopFailure" in controller
    assert "diRequestedAccelMilli" in controller and "diBacklashMilli <> 0" in controller
    assert "FC_DecodeI32(iHighWord := iD1012AccelHi" in main
    assert "FC_DecodeI32(iHighWord := iD1018BacklashHi" in main


def test_readme_documents_u32_reset_and_unsafe_stop_response() -> None:
    readme = (PLC / "README.md").read_text(encoding="utf-8")
    for token in ("raw u32", "decode_u32", "MC_Reset", "FAULT_STOP_UNSAFE", "4320 bytes", "360-write"):
        assert token in readme


def test_invalid_feedback_is_stop_fault_and_logger_never_consumes_invalid_position() -> None:
    controller = source("FB_TurntableControl.st")
    main = source("PRG_MAIN.st")
    assert "NOT bPositionValid OR NOT bVelocityValid" in controller
    assert "IF bPositionReadError OR NOT bPositionValid" in controller
    assert "IF bPositionValid AND NOT bPositionError THEN" in main
    assert "rCurrentPositionDeg := rLoggerPosition" in main
    assert "rPreviousPosition := rLoggerPosition" in main


def test_stop_error_releases_execute_and_reset_start_clear_fault_latches() -> None:
    controller = source("FB_TurntableControl.st")
    main = source("PRG_MAIN.st")
    assert "IF bStopDone OR bStopError THEN" in main
    for token in (
        "uiRunStatus := RUN_IDLE",
        "bFaultStopPending := FALSE",
        "uiPendingFaultCode := FAULT_NONE",
        "bStopLatched := FALSE",
    ):
        assert token in controller


def test_modbus_words_are_globally_declared_once_not_locally_redeclared() -> None:
    constants = source("Turntable_Constants.st")
    main = source("PRG_MAIN.st")
    assert "VAR_GLOBAL" in constants and "iD1000Mode" in constants and "aD2000Events" in constants
    assert "iD1000Mode," not in main


def test_no_immediate_stop_is_called_or_claimed() -> None:
    corpus = "\n".join(path.read_text(encoding="utf-8") for path in PLC.rglob("*") if path.is_file())
    mentions = [line for line in corpus.splitlines() if "MC_ImmediateStop" in line]
    assert all("not used" in line.lower() for line in mentions)


def test_constants_cover_register_contract_motion_contract_and_event_extent() -> None:
    constants = source("Turntable_Constants.st")
    for register in range(1000, 1020):
        assert f"D{register}" in constants
    for register in range(1100, 1119):
        assert f"D{register}" in constants
    for register in range(1200, 1207):
        assert f"D{register}" in constants
    for token in ("16#1234", "16#5678", "360", "6", "D4159", "50000", "5000", "10000", "-360.0", "360.0", "1.0", "2.0", "4.0", "5.0", "10.0", "DIRECTION_CW", "DIRECTION_CCW"):
        assert token in constants


def test_degree_logger_is_bounded_writes_six_words_and_preserves_unacked_buffer() -> None:
    logger = source("FB_DegreeLogger.st")
    for token in ("ARRAY[0..2159] OF INT", "FOR iCrossing := 1 TO 360", "bBufferReady", "udiNowTickMs - udiRunStartTickMs", "6 * uiEventCount", "uiEventCount < 360"):
        assert token in logger


def test_readme_covers_binding_configuration_safety_and_resource_limits() -> None:
    readme = (PLC / "README.md").read_text(encoding="utf-8")
    for token in ("D1000", "D1206", "D2000", "D4159", "INT", "PDO", "6040h", "607Ah", "6081h", "6083h", "6084h", "6060h", "6041h", "6064h", "6061h", "Axis_0", "linear", "degrees", "50:1", "23-bit", "EtherCAT", "1 ms", "MAIN", "9116", "Modbus TCP", "EtherNET1", "CN3", "Type-C", "physical emergency stop", "unloaded", "1 degrees/s", "resolution", "49.7", "compiler", "resource"):
        assert token in readme


def test_reference_texts_are_litest_sized_and_avoid_dynamic_allocation() -> None:
    for path in SRC.glob("*.st"):
        text = path.read_text(encoding="utf-8")
        assert len(text.splitlines()) < 1000, path
        assert "malloc" not in text.lower()
        assert "new " not in text.lower()
