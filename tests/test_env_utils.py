"""
Tests for environment variable utilities.

These tests verify correct handling of shell vs .env file variable resolution,
which is critical for CI/CD secret management.
"""

import os
import pytest
from unittest.mock import patch, MagicMock

from src.env_utils import (
    detect_env_mismatches,
    resolve_env_mismatches,
    ensure_env_consistency,
    get_api_key,
    validate_api_key,
)


@pytest.fixture
def temp_env_file(tmp_path):
    """Create a temporary .env file for testing."""
    env_file = tmp_path / ".env"
    return env_file


@pytest.fixture
def clean_env(monkeypatch):
    """Ensure test env vars are not set."""
    for key in ["TEST_API_KEY", "TEST_SECRET", "ANOTHER_KEY"]:
        monkeypatch.delenv(key, raising=False)


class TestDetectEnvMismatches:
    """Test detect_env_mismatches function."""

    def test_no_mismatches_when_both_empty(self, temp_env_file, clean_env):
        """No mismatches when key is in neither shell nor file."""
        temp_env_file.write_text("")
        result = detect_env_mismatches(["TEST_API_KEY"], str(temp_env_file))
        # Both are None, so they match (both unset)
        assert "TEST_API_KEY" not in result

    def test_mismatch_when_only_in_shell(self, temp_env_file, clean_env, monkeypatch):
        """Mismatch detected when only in shell."""
        monkeypatch.setenv("TEST_API_KEY", "shell_value")
        temp_env_file.write_text("")

        result = detect_env_mismatches(["TEST_API_KEY"], str(temp_env_file))

        assert "TEST_API_KEY" in result
        assert result["TEST_API_KEY"]["shell"] == "shell_value"
        assert result["TEST_API_KEY"]["file"] is None
        assert result["TEST_API_KEY"]["shell_set"] is True
        assert result["TEST_API_KEY"]["file_set"] is False

    def test_mismatch_when_only_in_file(self, temp_env_file, clean_env):
        """Mismatch detected when only in .env file."""
        temp_env_file.write_text("TEST_API_KEY=file_value")

        result = detect_env_mismatches(["TEST_API_KEY"], str(temp_env_file))

        assert "TEST_API_KEY" in result
        assert result["TEST_API_KEY"]["shell"] is None
        assert result["TEST_API_KEY"]["file"] == "file_value"
        assert result["TEST_API_KEY"]["shell_set"] is False
        assert result["TEST_API_KEY"]["file_set"] is True

    def test_mismatch_when_values_differ(self, temp_env_file, clean_env, monkeypatch):
        """Mismatch detected when both set but values differ."""
        monkeypatch.setenv("TEST_API_KEY", "shell_value")
        temp_env_file.write_text("TEST_API_KEY=file_value")

        result = detect_env_mismatches(["TEST_API_KEY"], str(temp_env_file))

        assert "TEST_API_KEY" in result
        assert result["TEST_API_KEY"]["shell"] == "shell_value"
        assert result["TEST_API_KEY"]["file"] == "file_value"

    def test_no_mismatch_when_values_match(self, temp_env_file, clean_env, monkeypatch):
        """No mismatch when shell and file have same value."""
        monkeypatch.setenv("TEST_API_KEY", "same_value")
        temp_env_file.write_text("TEST_API_KEY=same_value")

        result = detect_env_mismatches(["TEST_API_KEY"], str(temp_env_file))

        assert "TEST_API_KEY" not in result

    def test_handles_quoted_values(self, temp_env_file, clean_env, monkeypatch):
        """Handles quoted values in .env file."""
        monkeypatch.setenv("TEST_API_KEY", "value")
        temp_env_file.write_text('TEST_API_KEY="value"')

        result = detect_env_mismatches(["TEST_API_KEY"], str(temp_env_file))

        # Should match - quotes are stripped
        assert "TEST_API_KEY" not in result

    def test_handles_single_quoted_values(self, temp_env_file, clean_env, monkeypatch):
        """Handles single-quoted values in .env file."""
        monkeypatch.setenv("TEST_API_KEY", "value")
        temp_env_file.write_text("TEST_API_KEY='value'")

        result = detect_env_mismatches(["TEST_API_KEY"], str(temp_env_file))

        assert "TEST_API_KEY" not in result

    def test_handles_comments(self, temp_env_file, clean_env):
        """Comments in .env file are ignored."""
        temp_env_file.write_text("# Comment\nTEST_API_KEY=value\n# Another comment")

        result = detect_env_mismatches(["TEST_API_KEY"], str(temp_env_file))

        assert "TEST_API_KEY" in result
        assert result["TEST_API_KEY"]["file"] == "value"

    def test_handles_empty_lines(self, temp_env_file, clean_env):
        """Empty lines in .env file are ignored."""
        temp_env_file.write_text("\n\nTEST_API_KEY=value\n\n")

        result = detect_env_mismatches(["TEST_API_KEY"], str(temp_env_file))

        assert "TEST_API_KEY" in result

    def test_handles_missing_file(self, clean_env, monkeypatch):
        """Gracefully handles missing .env file."""
        monkeypatch.setenv("TEST_API_KEY", "shell_value")

        result = detect_env_mismatches(["TEST_API_KEY"], "/nonexistent/.env")

        # Should detect shell value with no file value
        assert "TEST_API_KEY" in result
        assert result["TEST_API_KEY"]["shell"] == "shell_value"
        assert result["TEST_API_KEY"]["file"] is None

    def test_multiple_keys(self, temp_env_file, clean_env, monkeypatch):
        """Handles multiple keys correctly."""
        monkeypatch.setenv("KEY1", "shell1")
        monkeypatch.setenv("KEY2", "same")
        temp_env_file.write_text("KEY1=file1\nKEY2=same\nKEY3=file3")

        result = detect_env_mismatches(["KEY1", "KEY2", "KEY3"], str(temp_env_file))

        assert "KEY1" in result  # Different values
        assert "KEY2" not in result  # Same values
        assert "KEY3" in result  # Only in file


class TestResolveEnvMismatches:
    """Test resolve_env_mismatches function."""

    def test_prefer_shell_by_default(self, temp_env_file, clean_env, monkeypatch):
        """Shell value preferred by default."""
        monkeypatch.setenv("TEST_API_KEY", "shell_value")
        temp_env_file.write_text("TEST_API_KEY=file_value")

        result = resolve_env_mismatches(["TEST_API_KEY"], env_file=str(temp_env_file))

        assert result["TEST_API_KEY"] == "shell_value"

    def test_prefer_file_when_requested(self, temp_env_file, clean_env, monkeypatch):
        """File value preferred when prefer_file=True."""
        monkeypatch.setenv("TEST_API_KEY", "shell_value")
        temp_env_file.write_text("TEST_API_KEY=file_value")

        result = resolve_env_mismatches(
            ["TEST_API_KEY"], prefer_file=True, env_file=str(temp_env_file)
        )

        assert result["TEST_API_KEY"] == "file_value"
        # Should also update environment
        assert os.environ["TEST_API_KEY"] == "file_value"

    def test_load_from_file_when_shell_empty(self, temp_env_file, clean_env):
        """Load from file and export when shell is empty."""
        temp_env_file.write_text("TEST_API_KEY=file_value")

        result = resolve_env_mismatches(["TEST_API_KEY"], env_file=str(temp_env_file))

        assert result["TEST_API_KEY"] == "file_value"
        # Should export to shell
        assert os.environ["TEST_API_KEY"] == "file_value"

    def test_none_when_neither_set(self, temp_env_file, clean_env):
        """Returns None when neither shell nor file has value."""
        temp_env_file.write_text("")

        result = resolve_env_mismatches(["TEST_API_KEY"], env_file=str(temp_env_file))

        assert result["TEST_API_KEY"] is None

    def test_no_mismatch_uses_current(self, temp_env_file, clean_env, monkeypatch):
        """When no mismatch, uses current shell value."""
        monkeypatch.setenv("TEST_API_KEY", "same_value")
        temp_env_file.write_text("TEST_API_KEY=same_value")

        result = resolve_env_mismatches(["TEST_API_KEY"], env_file=str(temp_env_file))

        assert result["TEST_API_KEY"] == "same_value"


class TestEnsureEnvConsistency:
    """Test ensure_env_consistency function."""

    def test_no_action_when_consistent(self, temp_env_file, clean_env, monkeypatch):
        """No action needed when consistent."""
        monkeypatch.setenv("TEST_API_KEY", "same")
        temp_env_file.write_text("TEST_API_KEY=same")

        # Should not raise
        ensure_env_consistency(["TEST_API_KEY"], env_file=str(temp_env_file))

    def test_resolves_mismatches(self, temp_env_file, clean_env, monkeypatch):
        """Resolves mismatches when they exist."""
        monkeypatch.setenv("TEST_API_KEY", "shell_value")
        temp_env_file.write_text("TEST_API_KEY=file_value")

        ensure_env_consistency(["TEST_API_KEY"], env_file=str(temp_env_file))

        # Shell value should win by default
        assert os.environ["TEST_API_KEY"] == "shell_value"

    def test_prefer_file_option(self, temp_env_file, clean_env, monkeypatch):
        """Respects prefer_file option."""
        monkeypatch.setenv("TEST_API_KEY", "shell_value")
        temp_env_file.write_text("TEST_API_KEY=file_value")

        ensure_env_consistency(
            ["TEST_API_KEY"], env_file=str(temp_env_file), prefer_file=True
        )

        assert os.environ["TEST_API_KEY"] == "file_value"


class TestGetApiKey:
    """Test get_api_key function."""

    def test_returns_shell_value(self, temp_env_file, clean_env, monkeypatch):
        """Returns shell value when set."""
        monkeypatch.setenv("TEST_API_KEY", "shell_key")
        temp_env_file.write_text("TEST_API_KEY=file_key")

        result = get_api_key("TEST_API_KEY", env_file=str(temp_env_file))

        assert result == "shell_key"

    def test_returns_file_value_when_shell_empty(self, temp_env_file, clean_env):
        """Returns file value when shell is empty."""
        temp_env_file.write_text("TEST_API_KEY=file_key")

        result = get_api_key("TEST_API_KEY", env_file=str(temp_env_file))

        assert result == "file_key"

    def test_raises_when_not_found(self, temp_env_file, clean_env):
        """Raises RuntimeError when key not found."""
        temp_env_file.write_text("")

        with pytest.raises(RuntimeError) as exc_info:
            get_api_key("TEST_API_KEY", env_file=str(temp_env_file))

        assert "TEST_API_KEY not found" in str(exc_info.value)


class TestValidateApiKey:
    """Test validate_api_key function."""

    @patch("requests.get")
    def test_valid_key_returns_true(self, mock_get, temp_env_file, clean_env, monkeypatch):
        """Returns True for valid API key."""
        monkeypatch.setenv("TEST_API_KEY", "valid_key")
        temp_env_file.write_text("")
        mock_get.return_value = MagicMock(status_code=200)

        result = validate_api_key("TEST_API_KEY")

        assert result is True
        mock_get.assert_called_once()
        # Verify auth header was set
        call_args = mock_get.call_args
        assert "Bearer valid_key" in call_args.kwargs["headers"]["Authorization"]

    @patch("requests.get")
    def test_invalid_key_returns_false(self, mock_get, temp_env_file, clean_env, monkeypatch):
        """Returns False for invalid API key."""
        monkeypatch.setenv("TEST_API_KEY", "invalid_key")
        temp_env_file.write_text("")
        mock_get.return_value = MagicMock(status_code=401)

        result = validate_api_key("TEST_API_KEY")

        assert result is False

    @patch("requests.get")
    def test_network_error_returns_false(self, mock_get, temp_env_file, clean_env, monkeypatch):
        """Returns False on network error."""
        import requests as req_module
        monkeypatch.setenv("TEST_API_KEY", "valid_key")
        temp_env_file.write_text("")
        mock_get.side_effect = req_module.ConnectionError("Network error")

        result = validate_api_key("TEST_API_KEY")

        assert result is False

    def test_missing_key_returns_false(self, temp_env_file, clean_env):
        """Returns False when key is missing."""
        temp_env_file.write_text("")

        result = validate_api_key("TEST_API_KEY")

        assert result is False

    @patch("requests.get")
    def test_timeout_returns_false(self, mock_get, temp_env_file, clean_env, monkeypatch):
        """Returns False on timeout."""
        import requests as req_module
        monkeypatch.setenv("TEST_API_KEY", "valid_key")
        temp_env_file.write_text("")
        mock_get.side_effect = req_module.Timeout("Request timed out")

        result = validate_api_key("TEST_API_KEY")

        assert result is False


class TestEnvFileParsing:
    """Test edge cases in .env file parsing."""

    def test_equals_in_value(self, temp_env_file, clean_env):
        """Handles equals signs in values."""
        temp_env_file.write_text("TEST_KEY=value=with=equals")

        result = detect_env_mismatches(["TEST_KEY"], str(temp_env_file))

        assert result["TEST_KEY"]["file"] == "value=with=equals"

    def test_whitespace_around_key(self, temp_env_file, clean_env):
        """Handles whitespace around key."""
        temp_env_file.write_text("  TEST_KEY  =value")

        result = detect_env_mismatches(["TEST_KEY"], str(temp_env_file))

        assert result["TEST_KEY"]["file"] == "value"

    def test_whitespace_around_value(self, temp_env_file, clean_env):
        """Handles whitespace around value."""
        temp_env_file.write_text("TEST_KEY=  value  ")

        result = detect_env_mismatches(["TEST_KEY"], str(temp_env_file))

        assert result["TEST_KEY"]["file"] == "value"

    def test_line_without_equals(self, temp_env_file, clean_env):
        """Ignores malformed lines without equals."""
        temp_env_file.write_text("MALFORMED_LINE\nTEST_KEY=value")

        result = detect_env_mismatches(["TEST_KEY"], str(temp_env_file))

        assert result["TEST_KEY"]["file"] == "value"
