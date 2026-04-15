#!/usr/bin/env python3
"""
Simplified PR Security Audit for Bitbucket Pipelines
Runs Claude Code security audit on current working directory and outputs findings to stdout
"""

import os
import sys
import json
import subprocess
import time
from typing import Dict, Any, List, Tuple, Optional
from pathlib import Path

# Import existing components we can reuse
from claudecode.prompts import get_security_audit_prompt
from claudecode.findings_filter import FindingsFilter
from claudecode.json_parser import parse_json_with_fallbacks
from claudecode.constants import (
    EXIT_CONFIGURATION_ERROR,
    DEFAULT_CLAUDE_MODEL,
    EXIT_SUCCESS,
    EXIT_GENERAL_ERROR,
    SUBPROCESS_TIMEOUT
)
from claudecode.logger import get_logger
from claudecode.bitbucket_client import BitbucketClient

logger = get_logger(__name__)


class ConfigurationError(ValueError):
    """Raised when configuration is invalid or missing."""
    pass


class AuditError(ValueError):
    """Raised when security audit operations fail."""
    pass


class SimpleClaudeRunner:
    """Simplified Claude Code runner for Bitbucket Pipelines."""

    def __init__(self, timeout_minutes: Optional[int] = None):
        """Initialize Claude runner.

        Args:
            timeout_minutes: Timeout for Claude execution (defaults to SUBPROCESS_TIMEOUT)
        """
        if timeout_minutes is not None:
            self.timeout_seconds = timeout_minutes * 60
        else:
            self.timeout_seconds = SUBPROCESS_TIMEOUT

    def run_security_audit(self, repo_dir: Path, prompt: str) -> Tuple[bool, str, Dict[str, Any]]:
        """Run Claude Code security audit.

        Args:
            repo_dir: Path to repository directory
            prompt: Security audit prompt

        Returns:
            Tuple of (success, error_message, parsed_results)
        """
        if not repo_dir.exists():
            return False, f"Repository directory does not exist: {repo_dir}", {}

        # Check prompt size
        prompt_size = len(prompt.encode('utf-8'))
        if prompt_size > 1024 * 1024:  # 1MB
            print(f"[Warning] Large prompt size: {prompt_size / 1024 / 1024:.2f}MB", file=sys.stderr)

        try:
            cmd = [
                'claude',
                '--output-format', 'json',
                '--model', DEFAULT_CLAUDE_MODEL,
                '--disallowed-tools', 'Bash(ps:*)'
            ]

            NUM_RETRIES = 3
            for attempt in range(NUM_RETRIES):
                result = subprocess.run(
                    cmd,
                    input=prompt,
                    cwd=repo_dir,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds
                )

                if result.returncode != 0:
                    if attempt == NUM_RETRIES - 1:
                        error_details = f"Claude Code execution failed with return code {result.returncode}\n"
                        error_details += f"Stderr: {result.stderr}\n"
                        error_details += f"Stdout: {result.stdout[:500]}..."
                        return False, error_details, {}
                    else:
                        time.sleep(5 * attempt)
                        continue

                success, parsed_result = parse_json_with_fallbacks(result.stdout, "Claude Code output")

                if success:
                    if (isinstance(parsed_result, dict) and
                        parsed_result.get('type') == 'result' and
                        parsed_result.get('subtype') == 'success' and
                        parsed_result.get('is_error') and
                        parsed_result.get('result') == 'Prompt is too long'):
                        return False, "PROMPT_TOO_LONG", {}

                    if (isinstance(parsed_result, dict) and
                        parsed_result.get('type') == 'result' and
                        parsed_result.get('subtype') == 'error_during_execution' and
                        attempt == 0):
                        continue

                    parsed_results = self._extract_security_findings(parsed_result)
                    return True, "", parsed_results
                else:
                    if attempt == 0:
                        continue
                    else:
                        return False, "Failed to parse Claude output", {}

            return False, "Unexpected error in retry logic", {}

        except subprocess.TimeoutExpired:
            return False, f"Claude Code execution timed out after {self.timeout_seconds // 60} minutes", {}
        except Exception as e:
            return False, f"Claude Code execution error: {str(e)}", {}

    def _extract_security_findings(self, claude_output: Any) -> Dict[str, Any]:
        """Extract security findings from Claude's JSON response."""
        if isinstance(claude_output, dict):
            if 'result' in claude_output:
                result_text = claude_output['result']
                if isinstance(result_text, str):
                    success, result_json = parse_json_with_fallbacks(result_text, "Claude result text")
                    if success and result_json and 'findings' in result_json:
                        return result_json

        return {
            'findings': [],
            'analysis_summary': {
                'files_reviewed': 0,
                'high_severity': 0,
                'medium_severity': 0,
                'low_severity': 0,
                'review_completed': False,
            }
        }

    def validate_claude_available(self) -> Tuple[bool, str]:
        """Validate that Claude Code is available."""
        try:
            result = subprocess.run(
                ['claude', '--version'],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode == 0:
                api_key = os.environ.get('ANTHROPIC_API_KEY', '')
                if not api_key:
                    return False, "ANTHROPIC_API_KEY environment variable is not set"
                return True, ""
            else:
                error_msg = f"Claude Code returned exit code {result.returncode}"
                if result.stderr:
                    error_msg += f". Stderr: {result.stderr}"
                if result.stdout:
                    error_msg += f". Stdout: {result.stdout}"
                return False, error_msg

        except subprocess.TimeoutExpired:
            return False, "Claude Code command timed out"
        except FileNotFoundError:
            return False, "Claude Code is not installed or not in PATH"
        except Exception as e:
            return False, f"Failed to check Claude Code: {str(e)}"


def get_environment_config() -> Tuple[str, str, int]:
    """Get and validate environment configuration.

    Returns:
        Tuple of (workspace, repo_slug, pr_id)

    Raises:
        ConfigurationError: If required environment variables are missing or invalid
    """
    workspace = os.environ.get('BITBUCKET_WORKSPACE')
    repo_slug = os.environ.get('BITBUCKET_REPO_SLUG')
    pr_id_str = os.environ.get('BITBUCKET_PR_ID')

    if not workspace:
        raise ConfigurationError('BITBUCKET_WORKSPACE environment variable required')

    if not repo_slug:
        raise ConfigurationError('BITBUCKET_REPO_SLUG environment variable required')

    if not pr_id_str:
        raise ConfigurationError('BITBUCKET_PR_ID environment variable required')

    try:
        pr_id = int(pr_id_str)
    except ValueError:
        raise ConfigurationError(f'Invalid BITBUCKET_PR_ID: {pr_id_str}')

    return workspace, repo_slug, pr_id


def initialize_clients() -> Tuple[BitbucketClient, SimpleClaudeRunner]:
    """Initialize Bitbucket and Claude clients.

    Returns:
        Tuple of (bitbucket_client, claude_runner)

    Raises:
        ConfigurationError: If client initialization fails
    """
    try:
        bitbucket_client = BitbucketClient()
    except Exception as e:
        raise ConfigurationError(f'Failed to initialize Bitbucket client: {str(e)}')

    try:
        claude_runner = SimpleClaudeRunner()
    except Exception as e:
        raise ConfigurationError(f'Failed to initialize Claude runner: {str(e)}')

    return bitbucket_client, claude_runner


def initialize_findings_filter(custom_filtering_instructions: Optional[str] = None) -> FindingsFilter:
    """Initialize findings filter based on environment configuration.

    Args:
        custom_filtering_instructions: Optional custom filtering instructions

    Returns:
        FindingsFilter instance

    Raises:
        ConfigurationError: If filter initialization fails
    """
    try:
        use_claude_filtering = os.environ.get('ENABLE_CLAUDE_FILTERING', 'false').lower() == 'true'
        api_key = os.environ.get('ANTHROPIC_API_KEY')

        if use_claude_filtering and api_key:
            return FindingsFilter(
                use_hard_exclusions=True,
                use_claude_filtering=True,
                api_key=api_key,
                custom_filtering_instructions=custom_filtering_instructions
            )
        else:
            return FindingsFilter(
                use_hard_exclusions=True,
                use_claude_filtering=False
            )
    except Exception as e:
        raise ConfigurationError(f'Failed to initialize findings filter: {str(e)}')


def run_security_audit(claude_runner: SimpleClaudeRunner, prompt: str) -> Dict[str, Any]:
    """Run the security audit with Claude Code.

    Args:
        claude_runner: Claude runner instance
        prompt: The security audit prompt

    Returns:
        Audit results dictionary

    Raises:
        AuditError: If the audit fails
    """
    repo_path = os.environ.get('REPO_PATH')
    repo_dir = Path(repo_path) if repo_path else Path.cwd()
    success, error_msg, results = claude_runner.run_security_audit(repo_dir, prompt)

    if not success:
        raise AuditError(f'Security audit failed: {error_msg}')

    return results


def apply_findings_filter(findings_filter, original_findings: List[Dict[str, Any]],
                          pr_context: Dict[str, Any], client: BitbucketClient) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """Apply findings filter to reduce false positives.

    Args:
        findings_filter: Filter instance
        original_findings: Original findings from audit
        pr_context: PR context information
        client: Bitbucket client with exclusion logic

    Returns:
        Tuple of (kept_findings, excluded_findings, analysis_summary)
    """
    filter_success, filter_results, filter_stats = findings_filter.filter_findings(
        original_findings, pr_context
    )

    if filter_success:
        kept_findings = filter_results.get('filtered_findings', [])
        excluded_findings = filter_results.get('excluded_findings', [])
        analysis_summary = filter_results.get('analysis_summary', {})
    else:
        kept_findings = original_findings
        excluded_findings = []
        analysis_summary = {}

    final_kept_findings = []
    directory_excluded_findings = []

    for finding in kept_findings:
        if _is_finding_in_excluded_directory(finding, client):
            directory_excluded_findings.append(finding)
        else:
            final_kept_findings.append(finding)

    all_excluded_findings = excluded_findings + directory_excluded_findings
    analysis_summary['directory_excluded_count'] = len(directory_excluded_findings)

    return final_kept_findings, all_excluded_findings, analysis_summary


def _is_finding_in_excluded_directory(finding: Dict[str, Any], client: BitbucketClient) -> bool:
    """Check if a finding references a file in an excluded directory."""
    file_path = finding.get('file', '')
    if not file_path:
        return False
    return client._is_excluded(file_path)


def main():
    """Main execution function for Bitbucket Pipelines."""
    try:
        # Get environment configuration
        try:
            workspace, repo_slug, pr_id = get_environment_config()
        except ConfigurationError as e:
            print(json.dumps({'error': str(e)}))
            sys.exit(EXIT_CONFIGURATION_ERROR)

        # Load custom filtering instructions if provided
        custom_filtering_instructions = None
        filtering_file = os.environ.get('FALSE_POSITIVE_FILTERING_INSTRUCTIONS', '')
        if filtering_file and Path(filtering_file).exists():
            try:
                with open(filtering_file, 'r', encoding='utf-8') as f:
                    custom_filtering_instructions = f.read()
                    logger.info(f"Loaded custom filtering instructions from {filtering_file}")
            except Exception as e:
                logger.warning(f"Failed to read filtering instructions file {filtering_file}: {e}")

        # Load custom security scan instructions if provided
        custom_scan_instructions = None
        scan_file = os.environ.get('CUSTOM_SECURITY_SCAN_INSTRUCTIONS', '')
        if scan_file and Path(scan_file).exists():
            try:
                with open(scan_file, 'r', encoding='utf-8') as f:
                    custom_scan_instructions = f.read()
                    logger.info(f"Loaded custom security scan instructions from {scan_file}")
            except Exception as e:
                logger.warning(f"Failed to read security scan instructions file {scan_file}: {e}")

        # Initialize components
        try:
            bitbucket_client, claude_runner = initialize_clients()
        except ConfigurationError as e:
            print(json.dumps({'error': str(e)}))
            sys.exit(EXIT_CONFIGURATION_ERROR)

        # Initialize findings filter
        try:
            findings_filter = initialize_findings_filter(custom_filtering_instructions)
        except ConfigurationError as e:
            print(json.dumps({'error': str(e)}))
            sys.exit(EXIT_CONFIGURATION_ERROR)

        # Validate Claude Code is available
        claude_ok, claude_error = claude_runner.validate_claude_available()
        if not claude_ok:
            print(json.dumps({'error': f'Claude Code not available: {claude_error}'}))
            sys.exit(EXIT_GENERAL_ERROR)

        # Get PR data
        repo_name = f"{workspace}/{repo_slug}"
        try:
            pr_data = bitbucket_client.get_pr_data(workspace, repo_slug, pr_id)
            pr_diff = bitbucket_client.get_pr_diff(workspace, repo_slug, pr_id)
        except Exception as e:
            print(json.dumps({'error': f'Failed to fetch PR data: {str(e)}'}))
            sys.exit(EXIT_GENERAL_ERROR)

        # Generate security audit prompt
        prompt = get_security_audit_prompt(pr_data, pr_diff, custom_scan_instructions=custom_scan_instructions)

        # Run Claude Code security audit
        repo_path = os.environ.get('REPO_PATH')
        repo_dir = Path(repo_path) if repo_path else Path.cwd()
        success, error_msg, results = claude_runner.run_security_audit(repo_dir, prompt)

        # If prompt is too long, retry without diff
        if not success and error_msg == "PROMPT_TOO_LONG":
            print(f"[Info] Prompt too long, retrying without diff. Original prompt length: {len(prompt)} characters", file=sys.stderr)
            prompt_without_diff = get_security_audit_prompt(pr_data, pr_diff, include_diff=False, custom_scan_instructions=custom_scan_instructions)
            print(f"[Info] New prompt length: {len(prompt_without_diff)} characters", file=sys.stderr)
            success, error_msg, results = claude_runner.run_security_audit(repo_dir, prompt_without_diff)

        if not success:
            print(json.dumps({'error': f'Security audit failed: {error_msg}'}))
            sys.exit(EXIT_GENERAL_ERROR)

        # Filter findings to reduce false positives
        original_findings = results.get('findings', [])

        pr_context = {
            'repo_name': repo_name,
            'pr_number': pr_id,
            'title': pr_data.get('title', ''),
            'description': pr_data.get('body', '')
        }

        kept_findings, excluded_findings, analysis_summary = apply_findings_filter(
            findings_filter, original_findings, pr_context, bitbucket_client
        )

        # Prepare output
        output = {
            'pr_number': pr_id,
            'repo': repo_name,
            'findings': kept_findings,
            'analysis_summary': results.get('analysis_summary', {}),
            'filtering_summary': {
                'total_original_findings': len(original_findings),
                'excluded_findings': len(excluded_findings),
                'kept_findings': len(kept_findings),
                'filter_analysis': analysis_summary,
                'excluded_findings_details': excluded_findings
            }
        }

        # Output JSON to stdout
        print(json.dumps(output, indent=2))

        # Exit with appropriate code
        high_severity_count = len([f for f in kept_findings if f.get('severity', '').upper() == 'HIGH'])
        sys.exit(EXIT_GENERAL_ERROR if high_severity_count > 0 else EXIT_SUCCESS)

    except Exception as e:
        print(json.dumps({'error': f'Unexpected error: {str(e)}'}))
        sys.exit(EXIT_CONFIGURATION_ERROR)


if __name__ == '__main__':
    main()
