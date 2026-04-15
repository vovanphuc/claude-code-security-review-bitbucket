#!/usr/bin/env python3
"""
Bitbucket Cloud API client for PR security reviews.
"""

import os
import sys
import re
import base64
import requests
from typing import Dict, Any, List, Optional


class BitbucketClient:
    """Bitbucket Cloud API v2.0 client for Pipelines environment."""

    API_BASE = "https://api.bitbucket.org/2.0"

    def __init__(self):
        """Initialize Bitbucket client using environment variables.

        Supports two auth modes based on BITBUCKET_TOKEN format:
          - Contains ':' -> HTTP Basic auth (username:app_password)
          - Plain token   -> Bearer token (repository/workspace access token)
        """
        token = os.environ.get('BITBUCKET_TOKEN')
        if not token:
            raise ValueError("BITBUCKET_TOKEN environment variable required")

        if ':' in token:
            encoded = base64.b64encode(token.encode()).decode()
            self.headers = {
                'Authorization': f'Basic {encoded}',
                'Accept': 'application/json',
            }
        else:
            self.headers = {
                'Authorization': f'Bearer {token}',
                'Accept': 'application/json',
            }

        # Get excluded directories from environment
        exclude_dirs = os.environ.get('EXCLUDE_DIRECTORIES', '')
        self.excluded_dirs = [d.strip() for d in exclude_dirs.split(',') if d.strip()] if exclude_dirs else []
        if self.excluded_dirs:
            print(f"[Debug] Excluded directories: {self.excluded_dirs}", file=sys.stderr)

    def get_pr_data(self, workspace: str, repo_slug: str, pr_id: int) -> Dict[str, Any]:
        """Get PR metadata and changed files from Bitbucket API.

        Returns a normalized dict matching the internal format expected by prompts.py.
        """
        # Fetch PR metadata
        pr_url = f"{self.API_BASE}/repositories/{workspace}/{repo_slug}/pullrequests/{pr_id}"
        response = requests.get(pr_url, headers=self.headers)
        response.raise_for_status()
        pr = response.json()

        # Fetch diffstat (paginated) for changed files list
        files = self._get_diffstat(workspace, repo_slug, pr_id)

        # Compute aggregate stats from diffstat
        total_additions = sum(f['additions'] for f in files)
        total_deletions = sum(f['deletions'] for f in files)

        source = pr.get('source', {})
        destination = pr.get('destination', {})
        source_repo = source.get('repository', {})

        return {
            'number': pr['id'],
            'title': pr.get('title', ''),
            'body': pr.get('description', '') or '',
            'user': pr.get('author', {}).get('display_name', 'unknown'),
            'created_at': pr.get('created_on', ''),
            'updated_at': pr.get('updated_on', ''),
            'state': pr.get('state', ''),
            'head': {
                'ref': source.get('branch', {}).get('name', ''),
                'sha': source.get('commit', {}).get('hash', ''),
                'repo': {
                    'full_name': source_repo.get('full_name', f'{workspace}/{repo_slug}')
                }
            },
            'base': {
                'ref': destination.get('branch', {}).get('name', ''),
                'sha': destination.get('commit', {}).get('hash', ''),
            },
            'files': files,
            'additions': total_additions,
            'deletions': total_deletions,
            'changed_files': len(files),
        }

    def _get_diffstat(self, workspace: str, repo_slug: str, pr_id: int) -> List[Dict[str, Any]]:
        """Fetch all changed files via the diffstat endpoint (handles pagination)."""
        url: Optional[str] = (
            f"{self.API_BASE}/repositories/{workspace}/{repo_slug}"
            f"/pullrequests/{pr_id}/diffstat"
        )
        files: List[Dict[str, Any]] = []

        while url:
            response = requests.get(url, headers=self.headers)
            response.raise_for_status()
            data = response.json()

            for entry in data.get('values', []):
                new_file = entry.get('new')
                old_file = entry.get('old')
                filepath = (new_file or old_file or {}).get('path', '')

                if self._is_excluded(filepath):
                    continue

                status_map = {
                    'added': 'added',
                    'removed': 'removed',
                    'modified': 'modified',
                    'renamed': 'renamed',
                }
                raw_status = entry.get('status', 'modified')

                files.append({
                    'filename': filepath,
                    'status': status_map.get(raw_status, raw_status),
                    'additions': entry.get('lines_added', 0),
                    'deletions': entry.get('lines_removed', 0),
                    'changes': entry.get('lines_added', 0) + entry.get('lines_removed', 0),
                    'patch': '',  # Bitbucket diffstat does not include patches
                })

            url = data.get('next')

        return files

    def get_pr_diff(self, workspace: str, repo_slug: str, pr_id: int) -> str:
        """Get complete PR diff in unified format.

        Returns:
            Complete PR diff in unified format.
        """
        url = (
            f"{self.API_BASE}/repositories/{workspace}/{repo_slug}"
            f"/pullrequests/{pr_id}/diff"
        )
        headers = dict(self.headers)
        headers['Accept'] = 'text/plain'

        response = requests.get(url, headers=headers)
        response.raise_for_status()

        return self._filter_generated_files(response.text)

    # ------------------------------------------------------------------
    # Exclusion / filtering helpers (same logic as GitHubActionClient)
    # ------------------------------------------------------------------

    def _is_excluded(self, filepath: str) -> bool:
        """Check if a file should be excluded based on directory patterns."""
        for excluded_dir in self.excluded_dirs:
            if excluded_dir.startswith('./'):
                normalized_excluded = excluded_dir[2:]
            else:
                normalized_excluded = excluded_dir

            if filepath.startswith(excluded_dir + '/'):
                return True
            if filepath.startswith(normalized_excluded + '/'):
                return True
            if '/' + normalized_excluded + '/' in filepath:
                return True

        return False

    def _filter_generated_files(self, diff_text: str) -> str:
        """Filter out generated files and excluded directories from diff content."""
        file_sections = re.split(r'(?=^diff --git)', diff_text, flags=re.MULTILINE)
        filtered_sections = []

        for section in file_sections:
            if not section.strip():
                continue

            if ('@generated by' in section or
                '@generated' in section or
                'Code generated by OpenAPI Generator' in section or
                'Code generated by protoc-gen-go' in section):
                continue

            match = re.match(r'^diff --git a/(.*?) b/', section)
            if match:
                filename = match.group(1)
                if self._is_excluded(filename):
                    print(f"[Debug] Filtering out excluded file: {filename}", file=sys.stderr)
                    continue

            filtered_sections.append(section)

        return ''.join(filtered_sections)
