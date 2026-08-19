# AutoShop signed-integer PLC reference compatibility

## Purpose

Adapt the Easy521 AutoShop V4.12 PLC reference source to the data types shown
by the installed variable editor: `BOOL`, `BYTE`, `INT`, `DINT`, `REAL`, `IP`,
and `STRING`. The current reference uses IEC unsigned `UINT` and `UDINT`, which
the installed editor does not offer.

## Scope

- Keep the protocol-v2 address map unchanged: command `D0:D19`, status
  `D100:D120`, protocol `D200:D206`, and event words `D2000:D4159`.
- Use `INT` for all 16-bit wire words, state/command values, flags, counts, and
  sequence values. State/command values, flags, and counts are within the
  signed 16-bit range. Sequence values are raw 16-bit bit patterns and are
  compared only for equality/inequality, so their signed wraparound is safe.
- Use `DINT` for signed 32-bit process values and raw 32-bit tick bit patterns.
- Replace unsigned-only casts in the word codec with explicit signed-value
  correction so high-word-first register data keeps the same 16-bit bits.
- Update the PLC reference documentation and static contract tests to reject
  unsupported `UINT`/`UDINT` source declarations.

## Non-goals

- No change to PC Modbus addresses, CSV behavior, UI, speed settings, or
  commissioning order.
- No AutoShop download, PLC write, servo enable, EtherCAT change, or motion.
- No claim that the adapted source has compiled in the user's AutoShop until
  the user performs the controlled offline compilation.

## Validation

1. Add a failing static test that detects unsupported `UINT`/`UDINT` in the
   AutoShop reference sources and checks the signed codec rules.
2. Make the smallest source changes that satisfy it.
3. Run the focused PLC contract tests and the full PC test suite.
4. The user then records an AutoShop offline compilation result of `0 error`
   and `0 warning` before any request to download.
