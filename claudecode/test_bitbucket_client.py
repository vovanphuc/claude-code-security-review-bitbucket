#!/usr/bin/env python3
"""
Unit tests for BitbucketClient.
"""

import pytest
import os
from unittest.mock import Mock, patch

from claudecode.bitbucket_client import BitbucketClient


class TestBitbucketClient:
    """Test BitbucketClient functionality."""

    def test_init_requires_token(self):
        """Test that client initialization requires BITBUCKET_TOKEN."""
        original_token = os.environ.pop('BITBUCKET_TOKEN', None)
        try:
            with pytest.raises(ValueError, match="BITBUCKET_TOKEN environment variable required"):
                BitbucketClient()
        finally:
            if original_token:
                os.environ['BITBUCKET_TOKEN'] = original_token

    def test_init_with_bearer_token(self):
        """Test initialization with a bearer token (no colon)."""
        with patch.dict(os.environ, {'BITBUCKET_TOKEN': 'my-access-token'}):
            client = BitbucketClient()
            assert client.headers['Authorization'] == 'Bearer my-access-token'

    def test_init_with_basic_auth(self):
        """Test initialization with username:app_password."""
        with patch.dict(os.environ, {'BITBUCKET_TOKEN': 'user:app_password'}):
            client = BitbucketClient()
            assert client.headers['Authorization'].startswith('Basic ')
            # Verify the base64 encoding
            import base64
            encoded = base64.b64encode(b'user:app_password').decode()
            assert client.headers['Authorization'] == f'Basic {encoded}'

    @patch('requests.get')
    def test_get_pr_data_success(self, mock_get):
        """Test successful PR data retrieval."""
        pr_response = Mock()
        pr_response.json.return_value = {
            'id': 42,
            'title': 'Test PR',
            'description': 'PR description',
            'author': {'display_name': 'testuser'},
            'created_on': '2024-01-01T00:00:00Z',
            'updated_on': '2024-01-01T01:00:00Z',
            'state': 'OPEN',
            'source': {
                'branch': {'name': 'feature-branch'},
                'commit': {'hash': 'abc123'},
                'repository': {'full_name': 'workspace/repo'}
            },
            'destination': {
                'branch': {'name': 'main'},
                'commit': {'hash': 'def456'}
            },
        }
        pr_response.raise_for_status = Mock()

        diffstat_response = Mock()
        diffstat_response.json.return_value = {
            'values': [
                {
                    'new': {'path': 'src/main.py'},
                    'old': {'path': 'src/main.py'},
                    'status': 'modified',
                    'lines_added': 30,
                    'lines_removed': 5,
                },
                {
                    'new': {'path': 'tests/test_main.py'},
                    'old': None,
                    'status': 'added',
                    'lines_added': 20,
                    'lines_removed': 0,
                },
            ],
            'next': None,
        }
        diffstat_response.raise_for_status = Mock()

        mock_get.side_effect = [pr_response, diffstat_response]

        with patch.dict(os.environ, {'BITBUCKET_TOKEN': 'test-token'}):
            client = BitbucketClient()
            result = client.get_pr_data('workspace', 'repo', 42)

        assert mock_get.call_count == 2
        assert result['number'] == 42
        assert result['title'] == 'Test PR'
        assert result['body'] == 'PR description'
        assert result['user'] == 'testuser'
        assert len(result['files']) == 2
        assert result['files'][0]['filename'] == 'src/main.py'
        assert result['files'][1]['status'] == 'added'
        assert result['additions'] == 50
        assert result['deletions'] == 5
        assert result['changed_files'] == 2
        assert result['head']['ref'] == 'feature-branch'
        assert result['head']['sha'] == 'abc123'
        assert result['base']['ref'] == 'main'

    @patch('requests.get')
    def test_get_pr_data_null_description(self, mock_get):
        """Test PR data retrieval when description is null."""
        pr_response = Mock()
        pr_response.json.return_value = {
            'id': 10,
            'title': 'No Description',
            'description': None,
            'author': {'display_name': 'user'},
            'created_on': '', 'updated_on': '', 'state': 'OPEN',
            'source': {
                'branch': {'name': 'b'}, 'commit': {'hash': 'a'},
                'repository': {'full_name': 'w/r'}
            },
            'destination': {'branch': {'name': 'main'}, 'commit': {'hash': 'b'}},
        }
        pr_response.raise_for_status = Mock()

        diffstat_response = Mock()
        diffstat_response.json.return_value = {'values': [], 'next': None}
        diffstat_response.raise_for_status = Mock()

        mock_get.side_effect = [pr_response, diffstat_response]

        with patch.dict(os.environ, {'BITBUCKET_TOKEN': 'tok'}):
            client = BitbucketClient()
            result = client.get_pr_data('w', 'r', 10)

        assert result['body'] == ''

    @patch('requests.get')
    def test_get_pr_data_api_error(self, mock_get):
        """Test PR data retrieval with API error."""
        mock_response = Mock()
        mock_response.raise_for_status.side_effect = Exception("API Error")
        mock_get.return_value = mock_response

        with patch.dict(os.environ, {'BITBUCKET_TOKEN': 'tok'}):
            client = BitbucketClient()
            with pytest.raises(Exception, match="API Error"):
                client.get_pr_data('w', 'r', 1)

    @patch('requests.get')
    def test_get_pr_data_diffstat_pagination(self, mock_get):
        """Test that diffstat pagination is handled."""
        pr_response = Mock()
        pr_response.json.return_value = {
            'id': 1, 'title': 'PR', 'description': '',
            'author': {'display_name': 'u'},
            'created_on': '', 'updated_on': '', 'state': 'OPEN',
            'source': {'branch': {'name': 'b'}, 'commit': {'hash': 'a'},
                        'repository': {'full_name': 'w/r'}},
            'destination': {'branch': {'name': 'main'}, 'commit': {'hash': 'b'}},
        }
        pr_response.raise_for_status = Mock()

        page1 = Mock()
        page1.json.return_value = {
            'values': [{'new': {'path': 'file1.py'}, 'old': None, 'status': 'added',
                         'lines_added': 10, 'lines_removed': 0}],
            'next': 'https://api.bitbucket.org/2.0/repositories/w/r/pullrequests/1/diffstat?page=2',
        }
        page1.raise_for_status = Mock()

        page2 = Mock()
        page2.json.return_value = {
            'values': [{'new': {'path': 'file2.py'}, 'old': None, 'status': 'added',
                         'lines_added': 5, 'lines_removed': 0}],
            'next': None,
        }
        page2.raise_for_status = Mock()

        mock_get.side_effect = [pr_response, page1, page2]

        with patch.dict(os.environ, {'BITBUCKET_TOKEN': 'tok'}):
            client = BitbucketClient()
            result = client.get_pr_data('w', 'r', 1)

        assert len(result['files']) == 2
        assert result['files'][0]['filename'] == 'file1.py'
        assert result['files'][1]['filename'] == 'file2.py'

    @patch('requests.get')
    def test_get_pr_diff_success(self, mock_get):
        """Test successful PR diff retrieval."""
        diff_content = """diff --git a/src/main.py b/src/main.py
index abc123..def456 100644
--- a/src/main.py
+++ b/src/main.py
@@ -1,5 +1,10 @@
+import os
 def main():
     print("Hello")
+    process_data()
"""
        mock_response = Mock()
        mock_response.text = diff_content
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        with patch.dict(os.environ, {'BITBUCKET_TOKEN': 'tok'}):
            client = BitbucketClient()
            result = client.get_pr_diff('workspace', 'repo', 42)

        assert 'import os' in result
        assert 'process_data()' in result
        # Verify Accept header was set to text/plain
        call_headers = mock_get.call_args[1]['headers']
        assert call_headers['Accept'] == 'text/plain'

    @patch('requests.get')
    def test_get_pr_diff_filters_generated_files(self, mock_get):
        """Test that generated files are filtered from diff."""
        diff_with_generated = """diff --git a/src/main.py b/src/main.py
index abc123..def456 100644
--- a/src/main.py
+++ b/src/main.py
@@ -1,5 +1,10 @@
+import os
 def main():
     print("Hello")
diff --git a/generated/code.py b/generated/code.py
index 111..222 100644
--- a/generated/code.py
+++ b/generated/code.py
@@ -1,3 +1,5 @@
# @generated by protoc
+# More generated code
+print("generated")
diff --git a/src/feature.py b/src/feature.py
index 333..444 100644
--- a/src/feature.py
+++ b/src/feature.py
@@ -1,3 +1,5 @@
+# Real code
 def feature():
     pass
"""
        mock_response = Mock()
        mock_response.text = diff_with_generated
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        with patch.dict(os.environ, {'BITBUCKET_TOKEN': 'tok'}):
            client = BitbucketClient()
            result = client.get_pr_diff('w', 'r', 1)

        assert 'src/main.py' in result
        assert 'src/feature.py' in result
        assert 'generated/code.py' not in result

    def test_is_excluded(self):
        """Test directory exclusion logic."""
        with patch.dict(os.environ, {
            'BITBUCKET_TOKEN': 'tok',
            'EXCLUDE_DIRECTORIES': 'vendor,tests/fixtures'
        }):
            client = BitbucketClient()

            assert client._is_excluded('vendor/lib.py') is True
            assert client._is_excluded('tests/fixtures/data.json') is True
            assert client._is_excluded('src/main.py') is False
            assert client._is_excluded('tests/test_main.py') is False

    def test_filter_generated_files_edge_cases(self):
        """Test edge cases in generated file filtering."""
        with patch.dict(os.environ, {'BITBUCKET_TOKEN': 'tok'}):
            client = BitbucketClient()

            assert client._filter_generated_files('') == ''

            diff = """diff --git a/a.py b/a.py
@generated by tool
content
diff --git a/b.py b/b.py
normal content
diff --git a/c.py b/c.py
# This file is @generated
more content
"""
            result = client._filter_generated_files(diff)
            assert 'a.py' not in result
            assert 'b.py' in result
            assert 'c.py' not in result


class TestBitbucketAPIIntegration:
    """Test Bitbucket API integration scenarios."""

    @patch('requests.get')
    def test_rate_limit_handling(self, mock_get):
        """Test handling of API errors."""
        mock_response = Mock()
        mock_response.raise_for_status.side_effect = Exception("Rate limit exceeded")
        mock_get.return_value = mock_response

        with patch.dict(os.environ, {'BITBUCKET_TOKEN': 'tok'}):
            client = BitbucketClient()
            with pytest.raises(Exception, match="Rate limit exceeded"):
                client.get_pr_data('w', 'r', 1)

    @patch('requests.get')
    def test_excluded_files_in_diffstat(self, mock_get):
        """Test that excluded files are filtered from diffstat results."""
        pr_response = Mock()
        pr_response.json.return_value = {
            'id': 1, 'title': 'PR', 'description': '',
            'author': {'display_name': 'u'},
            'created_on': '', 'updated_on': '', 'state': 'OPEN',
            'source': {'branch': {'name': 'b'}, 'commit': {'hash': 'a'},
                        'repository': {'full_name': 'w/r'}},
            'destination': {'branch': {'name': 'main'}, 'commit': {'hash': 'b'}},
        }
        pr_response.raise_for_status = Mock()

        diffstat_response = Mock()
        diffstat_response.json.return_value = {
            'values': [
                {'new': {'path': 'src/main.py'}, 'old': None, 'status': 'added',
                 'lines_added': 10, 'lines_removed': 0},
                {'new': {'path': 'vendor/lib.py'}, 'old': None, 'status': 'added',
                 'lines_added': 100, 'lines_removed': 0},
            ],
            'next': None,
        }
        diffstat_response.raise_for_status = Mock()

        mock_get.side_effect = [pr_response, diffstat_response]

        with patch.dict(os.environ, {
            'BITBUCKET_TOKEN': 'tok',
            'EXCLUDE_DIRECTORIES': 'vendor'
        }):
            client = BitbucketClient()
            result = client.get_pr_data('w', 'r', 1)

        assert len(result['files']) == 1
        assert result['files'][0]['filename'] == 'src/main.py'
