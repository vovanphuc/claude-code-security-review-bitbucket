#!/usr/bin/env python3
"""
Integration tests for full ClaudeCode workflow (Bitbucket version).
"""

import pytest
import json
import os
import tempfile
from unittest.mock import Mock, patch
from pathlib import Path

from claudecode.bitbucket_pipeline_audit import main


class TestFullWorkflowIntegration:
    """Test complete workflow scenarios."""

    @patch('claudecode.bitbucket_pipeline_audit.subprocess.run')
    @patch('requests.get')
    def test_full_workflow_with_real_pr_structure(self, mock_get, mock_run):
        """Test complete workflow with realistic PR data."""
        # Setup Bitbucket API responses
        pr_response = Mock()
        pr_response.json.return_value = {
            'id': 456,
            'title': 'Add new authentication feature',
            'description': 'This PR adds OAuth2 authentication support',
            'author': {'display_name': 'developer'},
            'created_on': '2024-01-15T10:00:00Z',
            'updated_on': '2024-01-15T14:30:00Z',
            'state': 'OPEN',
            'source': {
                'branch': {'name': 'feature/oauth2'},
                'commit': {'hash': 'abc123def456'},
                'repository': {'full_name': 'company/app'}
            },
            'destination': {
                'branch': {'name': 'main'},
                'commit': {'hash': 'main123'}
            },
        }
        pr_response.raise_for_status = Mock()

        diffstat_response = Mock()
        diffstat_response.json.return_value = {
            'values': [
                {
                    'new': {'path': 'src/auth/oauth2.py'},
                    'old': None,
                    'status': 'added',
                    'lines_added': 150,
                    'lines_removed': 0,
                },
                {
                    'new': {'path': 'src/auth/config.py'},
                    'old': {'path': 'src/auth/config.py'},
                    'status': 'modified',
                    'lines_added': 20,
                    'lines_removed': 10,
                }
            ],
            'next': None,
        }
        diffstat_response.raise_for_status = Mock()

        diff_response = Mock()
        diff_response.text = '''diff --git a/src/auth/oauth2.py b/src/auth/oauth2.py
new file mode 100644
--- /dev/null
+++ b/src/auth/oauth2.py
@@ -0,0 +1,150 @@
+import requests
+import jwt
+
+class OAuth2Handler:
+    def authenticate(self, username, password):
+        query = "SELECT * FROM users WHERE username='" + username + "'"
diff --git a/src/auth/config.py b/src/auth/config.py
--- a/src/auth/config.py
+++ b/src/auth/config.py
@@ -10,5 +10,15 @@
-SECRET_KEY = "old-secret"
+SECRET_KEY = "MySecretKey123!"
'''
        diff_response.raise_for_status = Mock()

        mock_get.side_effect = [pr_response, diffstat_response, diff_response]

        # Setup Claude response
        claude_response = {
            "findings": [
                {
                    "file": "src/auth/oauth2.py",
                    "line": 11,
                    "severity": "HIGH",
                    "category": "sql_injection",
                    "description": "SQL injection vulnerability",
                    "exploit_scenario": "Attacker could inject SQL",
                    "recommendation": "Use parameterized queries",
                    "confidence": 0.95
                },
                {
                    "file": "src/auth/config.py",
                    "line": 12,
                    "severity": "HIGH",
                    "category": "hardcoded_secrets",
                    "description": "Hardcoded secret key",
                    "exploit_scenario": "Anyone with code access can see the secret",
                    "recommendation": "Use environment variables",
                    "confidence": 0.99
                },
            ],
            "analysis_summary": {
                "files_reviewed": 2,
                "high_severity": 2,
                "medium_severity": 0,
                "low_severity": 0,
                "review_completed": True
            }
        }

        version_result = Mock()
        version_result.returncode = 0
        version_result.stdout = 'claude version 1.0.0'
        version_result.stderr = ''

        audit_result = Mock()
        audit_result.returncode = 0
        claude_wrapped_response = {
            'result': json.dumps(claude_response)
        }
        audit_result.stdout = json.dumps(claude_wrapped_response)
        audit_result.stderr = ''

        mock_run.side_effect = [version_result, audit_result, audit_result]

        with tempfile.TemporaryDirectory() as tmpdir:
            os.chdir(tmpdir)

            with patch.dict(os.environ, {
                'BITBUCKET_WORKSPACE': 'company',
                'BITBUCKET_REPO_SLUG': 'app',
                'BITBUCKET_PR_ID': '456',
                'BITBUCKET_TOKEN': 'test-token',
                'ANTHROPIC_API_KEY': 'test-api-key',
                'ENABLE_CLAUDE_FILTERING': 'false'
            }):
                with pytest.raises(SystemExit) as exc_info:
                    main()

                assert exc_info.value.code == 1  # HIGH severity findings

        assert mock_get.call_count == 3
        assert mock_run.call_count == 2

        audit_call = mock_run.call_args_list[1]
        prompt = audit_call[1]['input']
        assert 'Add new authentication feature' in prompt
        assert 'src/auth/oauth2.py' in prompt

    @patch('subprocess.run')
    @patch('requests.get')
    def test_workflow_with_no_security_issues(self, mock_get, mock_run):
        """Test workflow when no security issues are found."""
        pr_response = Mock()
        pr_response.json.return_value = {
            'id': 999,
            'title': 'Add documentation',
            'description': 'Updates to README',
            'author': {'display_name': 'docs-team'},
            'created_on': '2024-01-25T11:00:00Z',
            'updated_on': '2024-01-25T11:05:00Z',
            'state': 'OPEN',
            'source': {'branch': {'name': 'docs/update'}, 'commit': {'hash': 'doc123'},
                        'repository': {'full_name': 'company/app'}},
            'destination': {'branch': {'name': 'main'}, 'commit': {'hash': 'main789'}},
        }
        pr_response.raise_for_status = Mock()

        diffstat_response = Mock()
        diffstat_response.json.return_value = {
            'values': [
                {'new': {'path': 'README.md'}, 'old': {'path': 'README.md'},
                 'status': 'modified', 'lines_added': 40, 'lines_removed': 10}
            ],
            'next': None,
        }
        diffstat_response.raise_for_status = Mock()

        diff_response = Mock()
        diff_response.text = 'diff --git a/README.md b/README.md\n+## Installation\n+npm install\n'
        diff_response.raise_for_status = Mock()

        mock_get.side_effect = [pr_response, diffstat_response, diff_response]

        mock_run.side_effect = [
            Mock(returncode=0, stdout='claude version 1.0.0', stderr=''),
            Mock(returncode=0, stdout='{"findings": [], "analysis_summary": {"review_completed": true}}', stderr='')
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            os.chdir(tmpdir)

            output_file = Path(tmpdir) / 'output.json'

            with patch.dict(os.environ, {
                'BITBUCKET_WORKSPACE': 'company',
                'BITBUCKET_REPO_SLUG': 'app',
                'BITBUCKET_PR_ID': '999',
                'BITBUCKET_TOKEN': 'test-token',
                'ANTHROPIC_API_KEY': 'test-api-key'
            }):
                with patch('sys.stdout', open(output_file, 'w')):
                    with pytest.raises(SystemExit) as exc_info:
                        main()

                assert exc_info.value.code == 0

            with open(output_file) as f:
                output = json.load(f)

            assert output['pr_number'] == 999
            assert output['repo'] == 'company/app'
            assert len(output['findings']) == 0

    def test_workflow_error_recovery(self):
        """Test workflow recovery from various errors."""
        with patch('requests.get') as mock_get:
            mock_get.side_effect = Exception("Network error")

            with patch.dict(os.environ, {
                'BITBUCKET_WORKSPACE': 'workspace',
                'BITBUCKET_REPO_SLUG': 'repo',
                'BITBUCKET_PR_ID': '123',
                'BITBUCKET_TOKEN': 'token'
            }):
                with pytest.raises(SystemExit) as exc_info:
                    main()

                assert exc_info.value.code == 1
