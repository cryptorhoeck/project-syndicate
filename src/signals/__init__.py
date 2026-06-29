"""Signal providers — deterministic technical-analysis tools agents can consult.

These are ADVISORY ONLY. They return structured observations (`TechnicalSignal`)
for an agent's reasoning to weigh — data into the prompt. They NEVER place trades
and NEVER touch the exchange or the Warden. Execution remains the exclusive job of
the Warden-gated trading service (`src/trading/execution_service.py`).

This honours Project Syndicate's design: agents discover and decide; deterministic
code informs but does not direct (cf. the Wire's "auto-signals — never" rule).
"""

__version__ = "0.1.0"
