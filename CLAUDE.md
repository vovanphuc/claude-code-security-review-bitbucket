# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A Bitbucket Pipelines integration that uses Claude Code to perform AI-powered security reviews of pull requests. It fetches PR diffs via Bitbucket Cloud API v2.0, runs Claude Code for security analysis, filters false positives (hard rules + Claude API), and posts findings as PR comments.

## Commands

### Python Tests
```bash
# Run all tests with coverage (this is what CI runs)
pytest claudecode -v --cov=claudecode --cov-report=term-missing

# Run a single test file
pytest claudecode/test_bitbucket_client.py -v

# Run a specific test
pytest claudecode/test_bitbucket_client.py::TestBitbucketClient::test_get_pr_data_success -v
```

Note: pytest.ini sets `testpaths = tests` but tests actually live in `claudecode/test_*.py`. CI overrides this by passing `pytest claudecode` explicitly.

### JavaScript Tests (PR commenting script)
```bash
cd scripts
bun install
bun test
```

### Install Dependencies
```bash
pip install -r claudecode/requirements.txt pytest pytest-cov
```

## Architecture

### Pipeline Flow
```
PR opened → bitbucket_pipeline_audit.py fetches PR data/diff via Bitbucket API v2.0
  → prompts.py builds security audit prompt with diff + PR context
  → SimpleClaudeRunner executes Claude Code CLI subprocess (3 retries)
  → json_parser.py extracts JSON findings from Claude's response
  → findings_filter.py removes false positives (hard regex rules, then Claude API)
  → Results written to findings.json
  → comment-pr-findings.js posts findings to PR via Bitbucket REST API
```

### Key Modules (`claudecode/`)
- **bitbucket_pipeline_audit.py** — Main orchestration: environment config, `SimpleClaudeRunner` (Claude CLI execution with retry/fallback). If prompt is too long, retries without diff.
- **bitbucket_client.py** — `BitbucketClient` class: PR data fetching via Bitbucket Cloud API v2.0, diff retrieval, directory exclusion. Supports both Basic auth (username:app_password) and Bearer token auth.
- **prompts.py** — Builds the security audit prompt with PR metadata, diff content, security categories, and enforces JSON output schema.
- **findings_filter.py** — Two-stage false positive removal: `HardExclusionRules` (regex patterns for DOS, rate limiting, memory leaks, etc.) then Claude API semantic filtering.
- **claude_api_client.py** — Anthropic SDK wrapper for the API-based false positive filtering stage.
- **json_parser.py** — Robust JSON extraction with fallbacks: direct parse → markdown code block extraction → manual brace-counting.
- **constants.py** — Exit codes (0=success, 1=findings/error, 2=config error), timeouts, model defaults.

### Scripts (`scripts/`)
- **comment-pr-findings.js** — Reads findings.json and posts structured review comments to Bitbucket PRs via REST API v2.0. Supports inline comments on diff lines with fallback to general PR comments.

### Pipeline Configuration (`bitbucket-pipelines.yml`)
Runs on all pull requests. Steps: install deps → run Python audit → process results → post PR comments. Artifacts: findings.json, claudecode-results.json, claudecode-error.log.

## Key Environment Variables
- `ANTHROPIC_API_KEY` — Required for both Claude Code CLI and Claude API filtering (set as Bitbucket repository variable)
- `BITBUCKET_TOKEN` — Auth for Bitbucket API (username:app_password for Basic auth, or access token for Bearer auth)
- `BITBUCKET_WORKSPACE` — Bitbucket workspace slug (auto-provided by Pipelines)
- `BITBUCKET_REPO_SLUG` — Repository slug (auto-provided by Pipelines)
- `BITBUCKET_PR_ID` — Target PR ID (auto-provided by Pipelines for PR pipelines)
- `CLAUDE_MODEL` — Override default model (default: `claude-opus-4-6`)

## Custom Slash Command
`.claude/commands/security-review.md` defines a `/security-review` command that analyzes local branch changes using git diff (no Bitbucket API needed).
