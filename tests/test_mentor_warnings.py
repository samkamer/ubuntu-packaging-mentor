# tests/test_mentor_warnings.py — tests for _format_detective_warnings in mentor.py
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mentor import _format_detective_warnings


class TestFormatDetectiveWarningsNone:
    """Returns None when there is nothing to warn about."""

    def test_empty_warnings_dict(self):
        assert _format_detective_warnings({}, "Beginner") is None

    def test_none_categories(self):
        assert _format_detective_warnings({}, "MOTU") is None

    def test_empty_categories(self):
        warnings = {
            "possible_false_negatives": [],
            "possible_false_positives": [],
            "name_corrections": [],
            "blocklisted": [],
        }
        assert _format_detective_warnings(warnings, "CoreDev") is None


class TestFormatDetectiveWarningsBeginner:
    """Beginner persona: plain English, action-oriented."""

    def _make_warnings(self):
        return {
            "possible_false_negatives": [
                {"pkg": "libbrotli-dev", "reason": "removed during dedup"}
            ],
            "possible_false_positives": [
                {"pkg": "libngtcp2-crypto-gnutls-dev", "reason": "competing ngtcp2/ namespace"}
            ],
            "name_corrections": [{"from": "libldap-dev", "to": "libldap2-dev"}],
            "blocklisted": [{"pkg": "libc6-dev", "reason": "always available via build-essential"}],
        }

    def test_returns_string(self):
        result = _format_detective_warnings(self._make_warnings(), "Beginner")
        assert isinstance(result, str)

    def test_contains_false_negative_package(self):
        result = _format_detective_warnings(self._make_warnings(), "Beginner")
        assert "libbrotli-dev" in result

    def test_contains_false_positive_package(self):
        result = _format_detective_warnings(self._make_warnings(), "Beginner")
        assert "libngtcp2-crypto-gnutls-dev" in result

    def test_contains_name_correction(self):
        result = _format_detective_warnings(self._make_warnings(), "Beginner")
        assert "libldap-dev" in result
        assert "libldap2-dev" in result

    def test_contains_blocklisted(self):
        result = _format_detective_warnings(self._make_warnings(), "Beginner")
        assert "libc6-dev" in result

    def test_no_policy_reference(self):
        result = _format_detective_warnings(self._make_warnings(), "Beginner")
        assert "Policy" not in result
        assert "§7.6" not in result


class TestFormatDetectiveWarningsMOTU:
    """MOTU persona: technical list with Policy reference."""

    def _make_warnings(self):
        return {
            "possible_false_negatives": [
                {"pkg": "libbrotli-dev", "reason": "removed during dedup"}
            ],
            "name_corrections": [{"from": "libldap-dev", "to": "libldap2-dev"}],
        }

    def test_contains_policy_reference(self):
        result = _format_detective_warnings(self._make_warnings(), "MOTU")
        assert "Policy" in result or "§7.6" in result

    def test_contains_package_name(self):
        result = _format_detective_warnings(self._make_warnings(), "MOTU")
        assert "libbrotli-dev" in result

    def test_contains_correction(self):
        result = _format_detective_warnings(self._make_warnings(), "MOTU")
        assert "libldap-dev" in result
        assert "libldap2-dev" in result


class TestFormatDetectiveWarningsCoredev:
    """CoreDev persona: raw JSON output."""

    def test_contains_json_structure(self):
        import json
        warnings = {
            "possible_false_negatives": [
                {"pkg": "libbrotli-dev", "reason": "removed during dedup"}
            ],
        }
        result = _format_detective_warnings(warnings, "CoreDev")
        assert result is not None
        # Should contain valid JSON somewhere in the output
        assert "libbrotli-dev" in result
        assert "possible_false_negatives" in result

    def test_no_plain_english_guidance(self):
        warnings = {
            "possible_false_negatives": [
                {"pkg": "libbrotli-dev", "reason": "removed during dedup"}
            ],
        }
        result = _format_detective_warnings(warnings, "CoreDev")
        # CoreDev gets raw data, not English explanation phrases
        assert "Add them back" not in result
        assert "usually" not in result.lower()
