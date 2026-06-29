"""Step 3b — dual-gated genome->prompt wiring tests.

The load-bearing test is the MIDDLE case: master ON + per-agent flag OFF -> NO block.
That's the combination proving the drifted population stays dark the moment the master
switch flips. The two easy cases (both-off, both-on) are necessary but not sufficient.
"""

from types import SimpleNamespace

import pytest

from src.agents.context_assembler import ContextAssembler
from src.common.config import config


def _assembler():
    return ContextAssembler.__new__(ContextAssembler)  # helper needs no ctor state


def _agent():
    return SimpleNamespace(type="scout", generation=1)


def _genome_rec(context_enabled=True, genome_data=None):
    return SimpleNamespace(
        context_enabled=context_enabled,
        genome_data=genome_data if genome_data is not None else {
            "signal_generation": {"rsi_oversold": 30, "rsi_overbought": 70},
            "behavioral": {"communication_expressiveness": 0.6, "sip_propensity": 0.1},
        },
    )


@pytest.fixture
def master_on(monkeypatch):
    monkeypatch.setattr(config, "genome_context_enabled", True)


@pytest.fixture
def master_off(monkeypatch):
    monkeypatch.setattr(config, "genome_context_enabled", False)


def test_master_switch_defaults_off():
    # Merging 3b must be a no-op: the master switch ships OFF.
    assert config.genome_context_enabled is False


def test_no_block_when_master_off(master_off):
    block = _assembler()._genome_context_block(_genome_rec(context_enabled=True), _agent())
    assert block == ""


def test_no_block_when_flag_off_even_though_master_on(master_on):
    # THE LOAD-BEARING CASE: master flipped ON, but a drifted agent's per-agent flag is
    # OFF -> it stays dark. This is what makes the master flip safe for the population.
    block = _assembler()._genome_context_block(_genome_rec(context_enabled=False), _agent())
    assert block == ""


def test_block_present_when_both_on(master_on):
    block = _assembler()._genome_context_block(_genome_rec(context_enabled=True), _agent())
    assert "YOUR STRATEGY GENOME" in block
    assert "rsi_oversold" in block  # the genome's trading values are rendered
    # Behavioral knobs are excluded — not trading instincts; self-knowledge withheld.
    assert "communication_expressiveness" not in block
    assert "sip_propensity" not in block


def test_no_block_when_only_behavioral_sections(master_on):
    # A genome with no trading sections renders nothing (behavioral never shown).
    rec = _genome_rec(context_enabled=True, genome_data={"behavioral": {"sip_propensity": 0.1}})
    assert _assembler()._genome_context_block(rec, _agent()) == ""


def test_no_block_when_genome_rec_none(master_on):
    assert _assembler()._genome_context_block(None, _agent()) == ""


def test_no_block_when_flag_is_null_failsafe(master_on):
    # A NULL/missing per-agent flag must read as disabled (fail-safe).
    block = _assembler()._genome_context_block(_genome_rec(context_enabled=None), _agent())
    assert block == ""


def test_no_block_when_no_genome_data(master_on):
    block = _assembler()._genome_context_block(_genome_rec(context_enabled=True, genome_data={}), _agent())
    assert block == ""
