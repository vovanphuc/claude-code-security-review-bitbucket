#!/usr/bin/env python3
"""
Pytest tests for Bitbucket integration components.
"""

import pytest
import json


class TestClaudeCodeAudit:
    """Test the main audit functionality."""

    @pytest.fixture
    def mock_env(self, monkeypatch):
        """Set up mock environment variables."""
        monkeypatch.setenv('BITBUCKET_WORKSPACE', 'workspace')
        monkeypatch.setenv('BITBUCKET_REPO_SLUG', 'repo')
        monkeypatch.setenv('BITBUCKET_PR_ID', '123')
        monkeypatch.setenv('BITBUCKET_TOKEN', 'mock-token')
        monkeypatch.setenv('ANTHROPIC_API_KEY', 'mock-api-key')

    def test_missing_environment_variables(self, monkeypatch, capsys):
        """Test behavior with missing environment variables."""
        from claudecode import bitbucket_pipeline_audit

        # Test missing BITBUCKET_WORKSPACE
        monkeypatch.delenv('BITBUCKET_WORKSPACE', raising=False)
        monkeypatch.delenv('BITBUCKET_REPO_SLUG', raising=False)
        monkeypatch.delenv('BITBUCKET_PR_ID', raising=False)
        with pytest.raises(SystemExit) as exc_info:
            bitbucket_pipeline_audit.main()
        assert exc_info.value.code == 2  # EXIT_CONFIGURATION_ERROR
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert 'BITBUCKET_WORKSPACE' in output['error']

        # Test missing BITBUCKET_PR_ID
        monkeypatch.setenv('BITBUCKET_WORKSPACE', 'workspace')
        monkeypatch.setenv('BITBUCKET_REPO_SLUG', 'repo')
        monkeypatch.delenv('BITBUCKET_PR_ID', raising=False)
        with pytest.raises(SystemExit) as exc_info:
            bitbucket_pipeline_audit.main()
        assert exc_info.value.code == 2  # EXIT_CONFIGURATION_ERROR
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert 'BITBUCKET_PR_ID' in output['error']

    def test_invalid_pr_id(self, monkeypatch, capsys):
        """Test behavior with invalid PR ID."""
        from claudecode import bitbucket_pipeline_audit

        monkeypatch.setenv('BITBUCKET_WORKSPACE', 'workspace')
        monkeypatch.setenv('BITBUCKET_REPO_SLUG', 'repo')
        monkeypatch.setenv('BITBUCKET_PR_ID', 'invalid')
        monkeypatch.setenv('BITBUCKET_TOKEN', 'mock-token')

        with pytest.raises(SystemExit) as exc_info:
            bitbucket_pipeline_audit.main()
        assert exc_info.value.code == 2  # EXIT_CONFIGURATION_ERROR
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert 'Invalid BITBUCKET_PR_ID' in output['error']


class TestEnvironmentSetup:
    """Test environment setup and configuration."""

    def test_anthropic_api_key_handling(self, monkeypatch):
        """Test handling of Anthropic API key."""
        from claudecode.bitbucket_pipeline_audit import SimpleClaudeRunner

        runner = SimpleClaudeRunner()

        # Test with API key set
        monkeypatch.setenv('ANTHROPIC_API_KEY', 'test-key')
        valid, error = runner.validate_claude_available()
        # Note: This will fail if claude CLI is not installed, which is OK
        if not valid and 'not installed' in error:
            pytest.skip("Claude CLI not installed")

        # Test without API key
        monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
        valid, error = runner.validate_claude_available()
        if 'not installed' not in error:
            assert not valid
            assert 'ANTHROPIC_API_KEY' in error


class TestFilteringIntegration:
    """Test the filtering system integration."""

    def test_full_filter_with_llm_disabled(self):
        """Test FindingsFilter with LLM filtering disabled."""
        from claudecode.findings_filter import FindingsFilter

        filter_instance = FindingsFilter(
            use_hard_exclusions=True,
            use_claude_filtering=False
        )

        test_findings = [
            {'description': 'SQL injection vulnerability', 'severity': 'HIGH'},
            {'description': 'Missing rate limiting', 'severity': 'MEDIUM'},
        ]

        success, results, stats = filter_instance.filter_findings(test_findings)

        assert success is True
        assert stats.total_findings == 2
        assert stats.kept_findings == 1
        assert stats.hard_excluded == 1
        assert stats.claude_excluded == 0
