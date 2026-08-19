# AutoShop Signed-Integer Compatibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Easy521 AutoShop LiteST reference source use only the data types available in AutoShop V4.12 while preserving protocol-v2 wire values.

**Architecture:** All 16-bit Modbus words, state codes, flags, and counts use signed `INT`; deployed state/flag/count values remain inside the signed range. Sequence values are raw 16-bit `INT` bit patterns and are compared only for equality/inequality, so signed wraparound does not affect acknowledgement matching. Signed `DINT` carries 32-bit process values and raw tick bit patterns. The codec explicitly reconstructs an unsigned low word from a negative `INT` and splits a signed `DINT` into two raw `INT` words without `UINT`/`UDINT` casts.

**Tech Stack:** AutoShop LiteST reference `.st` sources, Python `pytest` static contracts.

## Global Constraints

- Keep `D0:D19`, `D100:D120`, `D200:D206`, and `D2000:D4159` unchanged.
- Use only AutoShop table types `BOOL`, `BYTE`, `INT`, `DINT`, `REAL`, `IP`, and `STRING` in active PLC reference source.
- Do not access PLC, AutoShop, Ethernet, EtherCAT, servo, or motor hardware.
- Do not claim an AutoShop compilation result; that remains an operator's offline validation.

---

### Task 1: Lock the supported-type and wire-bit contract

**Files:**
- Modify: `pc/tests/test_plc_source_contract.py`

**Interfaces:**
- Consumes: text from `plc/src/*.st`.
- Produces: static assertions that prohibit `UINT`/`UDINT`, require `INT`/`DINT`, and specify signed decimal equivalents for `0x1234`, `0x5678`, and `0xC350`.

- [ ] **Step 1: Write the failing test**

Add a test that reads all active `.st` sources and asserts:

```python
assert not re.search(r"\\b(?:UINT|UDINT)\\b", active_sources)
assert re.search(r"PROTOCOL_VERSION\\s*:\\s*INT\\s*:=\\s*2\\s*;", constants)
assert "WORD_ORDER_PROBE_HIGH : INT := 4660" in constants
assert "WORD_ORDER_PROBE_LOW : INT := 22136" in constants
assert "IF diLowWord < 0 THEN diLowWord := diLowWord + 65536; END_IF;" in codec
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run:

```powershell
$env:PYTHONPATH=(Resolve-Path '.\\pc\\src').Path
$toolPy='F:\\Codex_PRJ\\天线平台旋转装置\\.worktrees\\turntable-control\\.venv\\Scripts\\python.exe'
& $toolPy -m pytest pc/tests/test_plc_source_contract.py -q
```

Expected: failure because the current sources contain `UINT` and `UDINT`.

- [ ] **Step 3: Commit the red test**

```powershell
git add pc/tests/test_plc_source_contract.py
git commit -m "test: specify AutoShop signed type contract"
```

### Task 2: Adapt the codec and PLC source declarations

**Files:**
- Modify: `plc/src/Turntable_Constants.st`
- Modify: `plc/src/Turntable_RegisterCodec.st`
- Modify: `plc/src/FB_TurntableControl.st`
- Modify: `plc/src/FB_DegreeLogger.st`
- Modify: `plc/src/PRG_MAIN.st`

**Interfaces:**
- Consumes: Task 1's static contract.
- Produces: only `INT`/`DINT` declarations, `FC_DecodeI32(iHighWord: INT, iLowWord: INT) -> DINT`, `FC_SplitI32(diValue: DINT)`, and `FC_SplitU32(diValue: DINT)` with raw high-word-first output.

- [ ] **Step 1: Replace unsupported declaration types**

Replace every active `UINT` declaration and cast with `INT`; replace every active `UDINT` declaration and cast with `DINT`. Keep deployed enums, flags, and counters below `32768`; preserve sequence values as raw 16-bit patterns used only for equality/inequality checks. Rename no Modbus variable or D address.

- [ ] **Step 2: Implement signed low-word decode**

Make the codec use this supported-type logic:

```iecst
diLowWord := INT_TO_DINT(iLowWord);
IF diLowWord < 0 THEN diLowWord := diLowWord + 65536; END_IF;
FC_DecodeI32 := INT_TO_DINT(iHighWord) * 65536 + diLowWord;
```

- [ ] **Step 3: Implement signed raw-word split**

Make `FC_SplitI32` and the tick splitter divide/modulo `DINT` values, correct a negative remainder by adding `65536` and subtracting one from the high word, then convert any word value `>= 32768` by subtracting `65536` before `DINT_TO_INT`.

- [ ] **Step 4: Run the focused contract tests to verify green**

Run:

```powershell
$env:PYTHONPATH=(Resolve-Path '.\\pc\\src').Path
$toolPy='F:\\Codex_PRJ\\天线平台旋转装置\\.worktrees\\turntable-control\\.venv\\Scripts\\python.exe'
& $toolPy -m pytest pc/tests/test_plc_source_contract.py -q
```

Expected: all PLC source contracts pass.

- [ ] **Step 5: Commit the source adaptation**

```powershell
git add plc/src pc/tests/test_plc_source_contract.py
git commit -m "fix: adapt PLC reference to AutoShop signed types"
```

### Task 3: Synchronize operator instructions and run regression verification

**Files:**
- Modify: `plc/README.md`
- Modify: `docs/AI_HANDOFF.md`

**Interfaces:**
- Consumes: the Task 2 supported-type source contract.
- Produces: instructions that tell the operator to use `INT`/`DINT`, to enter decimal `-15536`, `4660`, and `22136`, and to leave unsupported unsigned types out of AutoShop tables.

- [ ] **Step 1: Update the operator documentation**

Add a concise AutoShop V4.12 note stating that `UINT`/`UDINT` are unsupported, all 16-bit wire values use `INT`, and the three decimal initial values are:

```text
D0011 = -15536
D0201 = 4660
D0202 = 22136
```

- [ ] **Step 2: Run full verification**

Run:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
$env:PYTHONPATH=(Resolve-Path '.\\pc\\src').Path
$toolPy='F:\\Codex_PRJ\\天线平台旋转装置\\.worktrees\\turntable-control\\.venv\\Scripts\\python.exe'
& $toolPy -m pytest pc/tests
& $toolPy -m compileall -q pc/src
git diff --check
```

Expected: all tests pass, Python compile exits 0, and Git whitespace check exits 0.

- [ ] **Step 3: Commit documentation and verification result**

```powershell
git add plc/README.md docs/AI_HANDOFF.md
git commit -m "docs: explain AutoShop signed variable setup"
```
