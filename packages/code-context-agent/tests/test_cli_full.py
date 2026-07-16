"""Tests for --full mode CLI features."""

import shutil

import pytest

from code_context_agent.cli import _derive_mode, _preflight_check, _validate_flags


class TestPreflightCheck:
    def test_returns_dict_with_tool_status(self):
        result = _preflight_check()
        assert isinstance(result, dict)
        assert "ripgrep" in result
        assert "gitnexus" in result
        assert "repomix" in result

    def test_each_entry_has_available_flag(self):
        result = _preflight_check()
        for info in result.values():
            assert "available" in info
            assert isinstance(info["available"], bool)

    def test_ripgrep_detected_if_installed(self):
        """ripgrep should be available in dev environment."""
        result = _preflight_check()
        if shutil.which("rg"):
            assert result["ripgrep"]["available"] is True


class TestValidateFlags:
    def test_full_and_since_raises(self):
        with pytest.raises(SystemExit):
            _validate_flags(full=True, since="HEAD~5")

    def test_full_alone_allowed(self):
        _validate_flags(full=True)  # Should not raise

    def test_no_flags_allowed(self):
        _validate_flags(full=False, since="")  # Should not raise

    def test_since_alone_allowed(self):
        _validate_flags(full=False, since="HEAD~5")  # Should not raise


class TestDeriveMode:
    def test_no_flags_standard(self):
        assert _derive_mode(full=False, focus="", since="") == "standard"

    def test_full_flag(self):
        assert _derive_mode(full=True, focus="", since="") == "full"

    def test_focus_flag(self):
        assert _derive_mode(full=False, focus="auth", since="") == "focus"

    def test_since_flag(self):
        assert _derive_mode(full=False, focus="", since="HEAD~5") == "incremental"

    def test_full_plus_focus(self):
        assert _derive_mode(full=True, focus="auth", since="") == "full+focus"
