# Task 2 Report — Motion Rules and Modbus Contract

## Status

Completed locally without PLC, network, or hardware access.

## Files

- `plc/register-map.md`
- `pc/src/turntable_control/registers.py`
- `pc/src/turntable_control/domain.py`
- `pc/tests/test_registers.py`
- `pc/tests/test_domain.py`
- `docs/worklog/2026-08-11-development-log.md`
- `.superpowers/sdd/task-2-report.md`

## Test-first evidence

Before implementation, ran:

```powershell
.\.venv\Scripts\python.exe -m pytest pc/tests/test_registers.py pc/tests/test_domain.py -q
```

Result: expected collection failure (`2 errors`): `turntable_control.registers` and `turntable_control.domain` did not yet exist.

## Final verification

Ran:

```powershell
.\.venv\Scripts\python.exe -m pytest pc/tests/test_registers.py pc/tests/test_domain.py -q
```

Accurate result: `39 passed`.

Ran the acceptance suite plus the existing smoke tests:

```powershell
.\.venv\Scripts\python.exe -m pytest pc/tests/test_registers.py pc/tests/test_domain.py pc/tests/test_smoke.py -q
```

Accurate result: `41 passed`.

## Commit

`687b06559cce34f1bdab1d0dfdca86cada5c96d4` — `feat: define motion rules and modbus contract`

## Self-review

- `plc/register-map.md` is the sole documented address contract and matches `Register`.
- The codec accepts only signed i32 values and exactly two words; it encodes high word first.
- Tests cover required values, signed limits, word count, fixed speeds, RPM conversion, target behavior at zero/interior/both limits, and event-buffer endpoint `D4159`.
- Automatic motion rejects a limit-crossing full turn with the required Chinese message; manual motion clamps it.

## Concerns

- PLC Modbus word order remains a field verification item. The `0x12345678` probe must be checked before any hardware write.
- No Modbus connection, PLC access, or mechanical motion was attempted in this task.
