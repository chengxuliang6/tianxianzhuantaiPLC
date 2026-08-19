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


def function_body(text: str, name: str) -> str:
    match = re.search(
        rf"FUNCTION\s+{re.escape(name)}\s*:\s*\w+.*?END_FUNCTION",
        text,
        re.DOTALL,
    )
    assert match, f"missing {name}"
    return match.group(0)


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
    assert "udiElapsedMs : DINT" in logger
    assert "FC_SplitU32" in logger and "FC_SplitU32" in codec


def test_start_has_a_terminal_ack_freshness_and_latched_parameter_path() -> None:
    main = source("PRG_MAIN.st")
    controller = source("FB_TurntableControl.st")
    assert "uiStartAckSeq := uiStartSeq; bStartAccepted := TRUE" in controller
    assert "(uiHeartbeatAgeScans > 1000)" in controller
    assert "OR bUnsafeStopFailure" in controller
    assert "diRequestedAccelMilli" in controller and "diBacklashMilli <> 0" in controller
    assert "FC_DecodeI32(iHighWord := iD0012AccelHi" in main
    assert "FC_DecodeI32(iHighWord := iD0018BacklashHi" in main


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
    assert "VAR_GLOBAL" in constants and "iD0000Mode" in constants and "aD2000Events" in constants
    assert "iD0000Mode," not in main


def test_no_immediate_stop_is_called_or_claimed() -> None:
    corpus = "\n".join(path.read_text(encoding="utf-8") for path in PLC.rglob("*") if path.is_file())
    mentions = [line for line in corpus.splitlines() if "MC_ImmediateStop" in line]
    assert all("not used" in line.lower() for line in mentions)


def test_constants_cover_register_contract_motion_contract_and_event_extent() -> None:
    constants = source("Turntable_Constants.st")
    for register in range(0, 20):
        assert f"D{register:04d}" in constants
    for register in range(100, 121):
        assert f"D{register:04d}" in constants
    for register in range(200, 207):
        assert f"D{register:04d}" in constants
    for token in ("4660", "22136", "360", "6", "D4159", "50000", "5000", "10000", "-360.0", "360.0", "1.0", "2.0", "4.0", "5.0", "10.0", "DIRECTION_CW", "DIRECTION_CCW"):
        assert token in constants


def test_plc_publishes_latched_run_start_tick_as_high_word_first_u32() -> None:
    constants = source("Turntable_Constants.st")
    main = source("PRG_MAIN.st")
    readme = (PLC / "README.md").read_text(encoding="utf-8")

    for token in (
        "D0119_RUN_START_TICK_HI",
        "D0120_RUN_START_TICK_LO",
        "iD0119RunStartTickHi",
        "iD0120RunStartTickLo",
    ):
        assert token in constants
    assert re.search(
        r"FC_SplitU32\(diValue\s*:=\s*fbControl\.udiRunStartTickMs,\s*"
        r"iHighWord\s*=>\s*iD0119RunStartTickHi,\s*"
        r"iLowWord\s*=>\s*iD0120RunStartTickLo\)",
        main,
    )
    assert "D0119:D0120" in readme
    assert "RUN_START_TICK_MS" in readme


def test_degree_logger_is_bounded_writes_six_words_and_preserves_unacked_buffer() -> None:
    logger = source("FB_DegreeLogger.st")
    for token in ("ARRAY[0..2159] OF INT", "FOR iCrossing := 1 TO 360", "bBufferReady", "udiNowTickMs - udiRunStartTickMs", "6 * uiEventCount", "uiEventCount < 360"):
        assert token in logger


def test_readme_covers_binding_configuration_safety_and_resource_limits() -> None:
    readme = (PLC / "README.md").read_text(encoding="utf-8")
    for token in ("D0000", "D0206", "D2000", "D4159", "INT", "PDO", "6040h", "607Ah", "6081h", "6083h", "6084h", "6060h", "6041h", "6064h", "6061h", "Axis_0", "linear", "degrees", "50:1", "23-bit", "EtherCAT", "1 ms", "MAIN", "9116", "Modbus TCP", "EtherNET1", "CN3", "Type-C", "physical emergency stop", "unloaded", "1 degrees/s", "resolution", "49.7", "compiler", "resource"):
        assert token in readme


def test_docs_invalidate_persisted_event_words_after_restart_and_lock_properties() -> None:
    readme = " ".join((PLC / "README.md").read_text(encoding="utf-8").split())
    register_map = " ".join((PLC / "register-map.md").read_text(encoding="utf-8").split())
    restart_rule = (
        "After a PLC restart, raw D2000:D4159 words may persist, but D116 EVENT_COUNT, "
        "D117 EVENT_GENERATION, D118 RUN_STATUS, and the buffer-ready status flag reset. "
        "The old words are invalid and must never be exported or acknowledged."
    )
    assert restart_rule in readme
    assert restart_rule in register_map
    assert (
        "Bind the address, then verify AutoShop automatically displays non-retained/private "
        "for D0:D206 and retained/private for D2000:D4159. Do not override the fixed "
        "address-derived property."
    ) in readme


def test_plc_reference_uses_only_protocol_v2_variable_names() -> None:
    constants = source("Turntable_Constants.st")
    active_sources = "\n".join(
        path.read_text(encoding="utf-8") for path in SRC.glob("*.st")
    )
    expected = [
        *(f"iD{address:04d}{suffix}" for address, suffix in (
            (0, "Mode"), (1, "Direction"), (2, "SpeedIndex"), (3, "StartSeq"),
            (4, "StopSeq"), (5, "SetZeroSeq"), (6, "ResetFaultSeq"), (7, "PowerSeq"),
            (8, "Heartbeat"), (9, "BufferAckSeq"), (10, "RatioHi"), (11, "RatioLo"),
            (12, "AccelHi"), (13, "AccelLo"), (14, "DecelHi"), (15, "DecelLo"),
            (16, "StopDecelHi"), (17, "StopDecelLo"), (18, "BacklashHi"), (19, "BacklashLo"),
        )),
        *(f"iD{address:04d}{suffix}" for address, suffix in (
            (100, "RunState"), (101, "StatusFlags"), (102, "FaultCode"), (103, "PositionHi"),
            (104, "PositionLo"), (105, "TargetHi"), (106, "TargetLo"), (107, "VelocityHi"),
            (108, "VelocityLo"), (109, "HeartbeatEcho"), (110, "StartAck"), (111, "StopAck"),
            (112, "SetZeroAck"), (113, "ResetFaultAck"), (114, "PowerAck"), (115, "BufferAcked"),
            (116, "EventCount"), (117, "Generation"), (118, "RunStatus"), (119, "RunStartTickHi"),
            (120, "RunStartTickLo"),
        )),
        *(f"iD{address:04d}{suffix}" for address, suffix in (
            (200, "ProtocolVersion"), (201, "WordOrderHi"), (202, "WordOrderLo"),
            (203, "TimeSyncRequest"), (204, "TickHi"), (205, "TickLo"), (206, "TimeSyncResponse"),
        )),
        "aD2000Events",
    ]
    for name in expected:
        assert name in constants
    assert re.search(r"PROTOCOL_VERSION\s*:\s*INT\s*:=\s*2\s*;", constants)
    assert not re.search(r"\biD(?:100\d|101\d|110\d|111\d|1120|120\d)\w*\b", active_sources)
    assert "D1201:D1202" not in active_sources


def test_autoshop_source_uses_only_supported_signed_integer_types_and_wire_literals() -> None:
    active_sources = "\n".join(
        path.read_text(encoding="utf-8") for path in SRC.glob("*.st")
    )
    assert re.search(r"FUNCTION\s+FC_SplitU32\s*:\s*BOOL\s*\nVAR_INPUT\s*\n\s*diValue\s*:\s*DINT", source("Turntable_RegisterCodec.st"))
    constants = source("Turntable_Constants.st")
    codec = source("Turntable_RegisterCodec.st")

    assert not re.search(r"\b(?:UINT|UDINT)\b", active_sources)
    assert re.search(r"PROTOCOL_VERSION\s*:\s*INT\s*:=\s*2\s*;", constants)
    assert "WORD_ORDER_PROBE_HIGH : INT := 4660" in constants
    assert "WORD_ORDER_PROBE_LOW : INT := 22136" in constants
    assert "IF diLowWord < 0 THEN diLowWord := diLowWord + 65536; END_IF;" in codec


def test_signed_raw_rollovers_and_splitter_normalization_are_explicit() -> None:
    main = source("PRG_MAIN.st")
    logger = source("FB_DegreeLogger.st")
    codec = source("Turntable_RegisterCodec.st")

    assert re.search(
        r"IF\s+udiPlcTickMs\s*=\s*2147483647\s+THEN\s*"
        r"udiPlcTickMs\s*:=\s*-2147483648\s*;\s*ELSE\s*"
        r"udiPlcTickMs\s*:=\s*udiPlcTickMs\s*\+\s*1\s*;\s*END_IF",
        main,
    )
    assert re.search(
        r"IF\s+uiGeneration\s*=\s*32767\s+THEN\s*"
        r"uiGeneration\s*:=\s*-32768\s*;\s*ELSE\s*"
        r"uiGeneration\s*:=\s*uiGeneration\s*\+\s*1\s*;\s*END_IF",
        logger,
    )
    assert "diElapsedBeforeWrap" in logger and "diElapsedAfterWrap" in logger
    assert re.search(
        r"IF\s+\(udiNowTickMs\s*<\s*0\)\s+AND\s+"
        r"\(udiRunStartTickMs\s*>=\s*0\)\s+THEN.*?"
        r"diElapsedBeforeWrap\s*:=\s*2147483647\s*-\s*udiRunStartTickMs\s*;.*?"
        r"diElapsedAfterWrap\s*:=\s*udiNowTickMs\s*-\s*\(-2147483648\)\s*;.*?"
        r"IF\s+diElapsedBeforeWrap\s*<\s*2147483647\s*-\s*"
        r"diElapsedAfterWrap\s+THEN.*?"
        r"udiElapsedMs\s*:=\s*diElapsedBeforeWrap\s*\+\s*"
        r"diElapsedAfterWrap\s*\+\s*1\s*;.*?"
        r"udiElapsedMs\s*:=\s*-2147483648\s*\+",
        logger,
        re.DOTALL,
    )
    assert re.search(
        r"ELSIF\s+\(udiNowTickMs\s*>=\s*0\)\s+AND\s+"
        r"\(udiRunStartTickMs\s*<\s*0\)\s+THEN.*?"
        r"diElapsedBeforeWrap\s*:=\s*2147483647\s*-\s*"
        r"\(udiRunStartTickMs\s*-\s*\(-2147483648\)\)\s*;.*?"
        r"diElapsedAfterWrap\s*:=\s*udiNowTickMs\s*;.*?"
        r"IF\s+diElapsedBeforeWrap\s*<\s*2147483647\s*-\s*"
        r"diElapsedAfterWrap\s+THEN.*?"
        r"udiElapsedMs\s*:=\s*diElapsedBeforeWrap\s*\+\s*"
        r"diElapsedAfterWrap\s*\+\s*1\s*;.*?"
        r"udiElapsedMs\s*:=\s*-2147483648\s*\+\s*"
        r"\(diElapsedBeforeWrap\s*-\s*\(2147483647\s*-\s*"
        r"diElapsedAfterWrap\)\)",
        logger,
        re.DOTALL,
    )

    for name in ("FC_SplitI32", "FC_SplitU32"):
        splitter = function_body(codec, name)
        assert re.search(
            r"FUNCTION\s+" + name + r"\s*:\s*BOOL\s*\nVAR_INPUT\s*\n\s*"
            r"diValue\s*:\s*DINT\s*;",
            splitter,
        )
        assert "diHighWord := diValue / 65536;" in splitter
        assert "diLowWord := diValue MOD 65536;" in splitter
        assert re.search(
            r"IF\s+diLowWord\s*<\s*0\s+THEN\s*"
            r"diLowWord\s*:=\s*diLowWord\s*\+\s*65536\s*;\s*"
            r"diHighWord\s*:=\s*diHighWord\s*-\s*1\s*;\s*END_IF",
            splitter,
        )
        assert "IF diHighWord >= 32768 THEN diHighWord := diHighWord - 65536; END_IF;" in splitter
        assert "IF diLowWord >= 32768 THEN diLowWord := diLowWord - 65536; END_IF;" in splitter
        assert "iHighWord := DINT_TO_INT(diHighWord);" in splitter
        assert "iLowWord := DINT_TO_INT(diLowWord);" in splitter


def test_elapsed_raw_u32_reference_model_handles_signed_boundaries() -> None:
    dint_min = -2147483648
    dint_max = 2147483647

    def plc_elapsed_model(start: int, now: int) -> int:
        if now < 0 <= start:
            before_wrap = dint_max - start
            after_wrap = now - dint_min
        elif now >= 0 > start:
            before_wrap = dint_max - (start - dint_min)
            after_wrap = now
        else:
            return now - start

        if before_wrap < dint_max - after_wrap:
            return before_wrap + after_wrap + 1
        return dint_min + (before_wrap - (dint_max - after_wrap))

    def raw_u32_elapsed(start: int, now: int) -> int:
        raw = ((now & 0xFFFFFFFF) - (start & 0xFFFFFFFF)) & 0xFFFFFFFF
        return raw if raw <= dint_max else raw - 0x100000000

    for start, now, expected in (
        (dint_min, 0, dint_min),
        (-1, dint_max, dint_min),
        (dint_min, dint_max, -1),
        (dint_max, dint_min, 1),
    ):
        assert raw_u32_elapsed(start, now) == expected
        assert plc_elapsed_model(start, now) == expected


def test_reference_texts_are_litest_sized_and_avoid_dynamic_allocation() -> None:
    for path in SRC.glob("*.st"):
        text = path.read_text(encoding="utf-8")
        assert len(text.splitlines()) < 1000, path
        assert "malloc" not in text.lower()
        assert "new " not in text.lower()


def test_delivery_docs_state_only_the_protocol_v2_commissioning_contract() -> None:
    docs = {
        path: path.read_text(encoding="utf-8")
        for path in (
            ROOT / "README.md",
            ROOT / "docs" / "AI_HANDOFF.md",
            ROOT / "docs" / "on-site-commissioning-checklist.md",
            ROOT / "docs" / "verification-report.md",
            ROOT / "docs" / "delivery-manifest.md",
            PLC / "README.md",
        )
    }
    checklist = docs[ROOT / "docs" / "on-site-commissioning-checklist.md"]
    for token in ("D0:D19", "D100:D120", "D200:D206", "D2000:D4159", "协议版本 `2`", "D200:D202", "0x1234", "0x5678", "反转=-1"):
        assert token in checklist
    assert "重启后旧事件字无效" in checklist
    normalized_checklist = " ".join(checklist.split())
    compile_gate = "AutoShop 编译为 `0 error`、`0 warning`"
    authorization_gate = "操作员一次性明确授权通过 Type-C 下载协议 v2 程序"
    download_gate = "下载期间电脑端不得连接 Modbus、不得产生任何 PC 写入、不得使能伺服或运动"
    read_only_gate = "下载完成并重新连接后，首先只读 `D200:D202`"
    static_gate = "只读状态块 `D100:D120`，确认静态安全状态"
    write_gate = "以上下载后只读门禁全部通过前，禁止任何 PC Modbus 写入"
    controlled_motion = "首次空载、1°/s、极小位移验证"
    for token in (
        compile_gate,
        authorization_gate,
        download_gate,
        read_only_gate,
        static_gate,
        write_gate,
        controlled_motion,
    ):
        assert token in normalized_checklist
    assert (
        normalized_checklist.index(compile_gate)
        < normalized_checklist.index(authorization_gate)
        < normalized_checklist.index(download_gate)
        < normalized_checklist.index(read_only_gate)
        < normalized_checklist.index(static_gate)
        < normalized_checklist.index(write_gate)
        < normalized_checklist.index(controlled_motion)
    )

    plc_readme = docs[PLC / "README.md"]
    assert "approximately 1 degree CW/CCW small displacement" in plc_readme
    assert "Full-turn/event-count hardware verification is not approved in this phase" in plc_readme
    assert "one turn/event count" not in plc_readme

    obsolete_current_contract = re.compile(r"\bD(?:10\d{2}|11\d{2}|120[0-6])\b")
    for path, text in docs.items():
        assert not obsolete_current_contract.search(text), f"{path}: v1 D1000:D1206 is not a current v2 contract"
        assert "协议版本 `1`" not in text, f"{path}: protocol v1 is not a current v2 contract"


def test_plc_readme_defers_physical_direction_verification_to_controlled_commissioning() -> None:
    readme = (PLC / "README.md").read_text(encoding="utf-8")
    pre_commissioning, heading, post_commissioning = readme.partition("8. **Commissioning page.**")
    normalized_pre_commissioning = " ".join(pre_commissioning.split())
    normalized_post_commissioning = " ".join(post_commissioning.split())

    assert heading
    assert "Inspect the configured positive-direction mapping" in pre_commissioning
    assert "do not enable or command physical motion during this offline configuration" in normalized_pre_commissioning
    assert "physical direction" not in pre_commissioning
    assert "CW/CCW physical direction" in normalized_post_commissioning
    assert "approximately 1 degree CW/CCW small displacement" in post_commissioning

    normalized = " ".join(readme.split())
    offline_compile = "then compile offline to zero errors before considering a download."
    read_only_gate = "After that download, reconnect read-only and verify D0200 is `2` and D0201/D0202 are `0x1234`/`0x5678` before any PC Modbus write."
    download_authorization = "Download only while the user is present and documented safety preconditions are met."
    controlled_motion = "approximately 1 degree CW/CCW small displacement"
    assert normalized.index(offline_compile) < normalized.index(download_authorization) < normalized.index(read_only_gate) < normalized.index(controlled_motion)
