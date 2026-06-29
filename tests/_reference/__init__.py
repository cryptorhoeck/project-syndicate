"""Frozen reference copies of external code, used ONLY for parity tests.

Files here are verbatim snapshots of upstream sources (e.g. jj-bot's original
`vwap_calculator.py`) kept so our ported/refactored versions can be asserted
byte-for-byte equivalent in behaviour. They are NOT production code and must not
be imported by `src/`. Do not "tidy" them — their value is being unchanged.
"""
