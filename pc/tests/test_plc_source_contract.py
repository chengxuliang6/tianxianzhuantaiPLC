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
    assert re.search(r"IF\s+bStopDone\s+THEN\s*\n\s*bStopExecute\s*:=\s*FALSE", main)
    assert controller.index("STOP_SEQ") < controller.index("START_SEQ")


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
    for token in ("ARRAY[0..2159] OF INT", "FOR iCrossing := 1 TO 60", "bBufferReady", "udiNowTickMs - udiRunStartTickMs", "6 * uiEventCount", "uiEventCount < 360"):
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
