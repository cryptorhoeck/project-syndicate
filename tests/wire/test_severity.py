"""Severity adjudication tests."""

import pytest

from src.wire.digest.severity import (
    SeverityViolation,
    apply_severity_rules,
    bound_haiku_severity,
)


class TestBoundHaikuSeverity:
    def test_in_range_passthrough(self) -> None:
        assert bound_haiku_severity(3) == (3, False)

    def test_above_max_capped_to_4_not_5(self) -> None:
        assert bound_haiku_severity(5) == (4, True)
        assert bound_haiku_severity(99) == (4, True)

    def test_below_min_falls_back_to_1(self) -> None:
        assert bound_haiku_severity(0) == (1, False)
        assert bound_haiku_severity(-3) == (1, False)

    def test_non_int_falls_back_to_1(self) -> None:
        assert bound_haiku_severity("abc") == (1, False)
        assert bound_haiku_severity(None) == (1, False)


class TestApplySeverityRules:
    def test_deterministic_5_wins(self) -> None:
        result = apply_severity_rules(deterministic_severity=5, haiku_severity=2)
        assert result.severity == 5
        assert result.reason == "deterministic"
        assert not result.capped

    def test_deterministic_below_5_wins_over_haiku(self) -> None:
        result = apply_severity_rules(deterministic_severity=3, haiku_severity=4)
        assert result.severity == 3
        assert result.reason == "deterministic"

    def test_haiku_used_when_no_deterministic(self) -> None:
        result = apply_severity_rules(deterministic_severity=None, haiku_severity=2)
        assert result.severity == 2
        assert result.reason == "haiku"
        assert not result.capped

    def test_haiku_5_attempt_capped_to_4_with_violation_flag(self) -> None:
        result = apply_severity_rules(deterministic_severity=None, haiku_severity=5)
        assert result.severity == 4
        assert result.reason == "haiku_capped"
        assert result.capped is True

    def test_haiku_none_falls_back_trivial(self) -> None:
        result = apply_severity_rules(deterministic_severity=None, haiku_severity=None)
        assert result.severity == 1
        assert result.reason == "fallback"

    def test_deterministic_out_of_range_raises(self) -> None:
        with pytest.raises(SeverityViolation):
            apply_severity_rules(deterministic_severity=7, haiku_severity=2)
        with pytest.raises(SeverityViolation):
            apply_severity_rules(deterministic_severity=0, haiku_severity=2)
