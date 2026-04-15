# Claude Code Security Reviewer for Bitbucket

An AI-powered security review tool for **Bitbucket Pipelines** using Claude to analyze code changes for security vulnerabilities. This tool provides intelligent, context-aware security analysis for pull requests using Anthropic's Claude Code for deep semantic security analysis.

Based on the original [GitHub Action](https://github.com/anthropics/claude-code-security-review) by Anthropic. See their [blog post](https://www.anthropic.com/news/automate-security-reviews-with-claude-code) for more details.

## Features

- **AI-Powered Analysis**: Uses Claude's advanced reasoning to detect security vulnerabilities with deep semantic understanding
- **Diff-Aware Scanning**: Only analyzes changed files in the pull request
- **PR Comments**: Automatically posts inline comments on PRs with security findings
- **Contextual Understanding**: Goes beyond pattern matching to understand code semantics
- **Language Agnostic**: Works with any programming language
- **False Positive Filtering**: Two-stage filtering (hard rules + Claude API) to reduce noise

## Quick Start

### 1. Add the pipeline configuration

Copy `bitbucket-pipelines.yml` to your repository root (or merge with your existing one):

```yaml
image: python:3.11-slim

definitions:
  steps:
    - step: &security-review
        name: "Claude Code Security Review"
        size: 2x
        script:
          - apt-get update && apt-get install -y --no-install-recommends curl jq ca-certificates
          - curl -fsSL https://deb.nodesource.com/setup_18.x | bash -
          - apt-get install -y --no-install-recommends nodejs
          - pip install --no-cache-dir -r claudecode/requirements.txt
          - npm install -g @anthropic-ai/claude-code
          - |
            if [ -z "$ANTHROPIC_API_KEY" ]; then
              echo "ERROR: ANTHROPIC_API_KEY is not set"; exit 1
            fi
          - export REPO_PATH=$(pwd)
          - export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}$(pwd)"
          - python -u claudecode/bitbucket_pipeline_audit.py > claudecode-results.json 2>claudecode-error.log || true
          - jq '.findings // []' claudecode-results.json > findings.json || echo '[]' > findings.json
          - |
            if [ -n "$BITBUCKET_PR_ID" ] && [ -f findings.json ]; then
              node scripts/comment-pr-findings.js || echo "WARNING: Failed to post PR comments"
            fi
        artifacts:
          - claudecode-results.json
          - findings.json
          - claudecode-error.log

pipelines:
  pull-requests:
    '**':
      - step: *security-review
```

### 2. Set up repository variables

Go to **Repository Settings > Pipelines > Repository variables** and add:

| Variable | Value | Secured |
|----------|-------|---------|
| `ANTHROPIC_API_KEY` | Your Anthropic API key | Yes |
| `BITBUCKET_TOKEN` | Auth token for PR comments (see [Authentication](#authentication)) | Yes |

### 3. Enable Pipelines

Go to **Repository Settings > Pipelines > Settings** and toggle **Enable Pipelines** to ON.

### 4. Create a PR

Open a pull request — the security review runs automatically.

## Authentication

`BITBUCKET_TOKEN` is used to fetch PR data and post comments. Two formats are supported:

**Option A: App Password (recommended)**
1. Go to **Personal Settings > App passwords**
2. Create with permissions: **Repositories: Read**, **Pull requests: Read + Write**
3. Set `BITBUCKET_TOKEN` to `username:app_password`

**Option B: Repository Access Token**
1. Go to **Repository Settings > Security > Access tokens**
2. Create with **Pull requests: Read + Write** permission
3. Set `BITBUCKET_TOKEN` to the token value (no username prefix)

## Security Considerations

This tool is not hardened against prompt injection attacks and should only be used to review trusted PRs. For public repositories, ensure PRs from external contributors require approval before pipelines run.

## Configuration Options

### Repository Variables

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `ANTHROPIC_API_KEY` | Anthropic API key (must be enabled for both Claude API and Claude Code) | — | Yes |
| `BITBUCKET_TOKEN` | Bitbucket auth token for API access | — | Yes |
| `CLAUDE_MODEL` | Claude [model](https://docs.anthropic.com/en/docs/about-claude/models/overview#model-names) to use | `claude-opus-4-6` | No |
| `EXCLUDE_DIRECTORIES` | Comma-separated list of directories to skip | — | No |
| `ENABLE_CLAUDE_FILTERING` | Set `true` to enable Claude API false-positive filtering | `false` | No |
| `FALSE_POSITIVE_FILTERING_INSTRUCTIONS` | Path to custom filtering instructions file | — | No |
| `CUSTOM_SECURITY_SCAN_INSTRUCTIONS` | Path to custom scan instructions file | — | No |
| `SILENCE_CLAUDECODE_COMMENTS` | Set `true` to skip posting PR comments | `false` | No |

### Cost Optimization

To reduce API costs, use a smaller model:

```
CLAUDE_MODEL=claude-sonnet-4-6
```

This is ~5x cheaper than Opus while still providing effective security analysis.

## How It Works

### Architecture

```
claudecode/
├── bitbucket_pipeline_audit.py  # Main orchestration for Bitbucket Pipelines
├── bitbucket_client.py          # Bitbucket Cloud API v2.0 client
├── prompts.py                   # Security audit prompt templates
├── findings_filter.py           # False positive filtering logic
├── claude_api_client.py         # Claude API client for filtering
├── json_parser.py               # Robust JSON parsing utilities
├── constants.py                 # Exit codes, timeouts, model defaults
├── requirements.txt             # Python dependencies
├── test_*.py                    # Test suites
└── evals/                       # Eval tooling
scripts/
└── comment-pr-findings.js       # Posts findings as PR comments
```

### Pipeline Flow

```
PR opened
  → bitbucket_pipeline_audit.py fetches PR data/diff via Bitbucket API v2.0
  → prompts.py builds security audit prompt with diff + PR context
  → Claude Code CLI runs security analysis (3 retries, fallback without diff)
  → json_parser.py extracts JSON findings from Claude's response
  → findings_filter.py removes false positives (hard rules + optional Claude API)
  → Results written to findings.json
  → comment-pr-findings.js posts inline comments to PR via Bitbucket API
```

### Workflow

1. **PR Analysis**: When a pull request is opened, Claude analyzes the diff to understand what changed
2. **Contextual Review**: Claude examines the code changes in context, understanding the purpose and potential security implications
3. **Finding Generation**: Security issues are identified with detailed explanations, severity ratings, and remediation guidance
4. **False Positive Filtering**: Two-stage filtering removes low-impact or false positive findings
5. **PR Comments**: Findings are posted as inline comments on the specific lines of code

## Security Analysis Capabilities

### Types of Vulnerabilities Detected

- **Injection Attacks**: SQL injection, command injection, LDAP injection, XPath injection, NoSQL injection, XXE
- **Authentication & Authorization**: Broken authentication, privilege escalation, insecure direct object references, bypass logic, session flaws
- **Data Exposure**: Hardcoded secrets, sensitive data logging, information disclosure, PII handling violations
- **Cryptographic Issues**: Weak algorithms, improper key management, insecure random number generation
- **Input Validation**: Missing validation, improper sanitization, buffer overflows
- **Business Logic Flaws**: Race conditions, time-of-check-time-of-use (TOCTOU) issues
- **Configuration Security**: Insecure defaults, missing security headers, permissive CORS
- **Code Execution**: RCE via deserialization, eval injection
- **Cross-Site Scripting (XSS)**: Reflected, stored, and DOM-based XSS

### False Positive Filtering

The tool automatically excludes low-impact and false positive prone findings:
- Denial of Service vulnerabilities
- Rate limiting concerns
- Memory/CPU exhaustion issues
- Generic input validation without proven impact
- Open redirect vulnerabilities

The false positive filtering can be customized for your project's needs.

### Benefits Over Traditional SAST

- **Contextual Understanding**: Understands code semantics and intent, not just patterns
- **Lower False Positives**: AI-powered analysis reduces noise
- **Detailed Explanations**: Clear explanations of why something is vulnerable and how to fix it
- **Adaptive**: Can be customized with organization-specific security requirements

## Claude Code Integration: /security-review Command

Claude Code ships a `/security-review` [slash command](https://docs.anthropic.com/en/docs/claude-code/slash-commands) that provides the same security analysis locally. Run `/security-review` in Claude Code to review all pending changes.

### Customizing the Command

1. Copy [`security-review.md`](.claude/commands/security-review.md) to your project's `.claude/commands/` folder
2. Edit to customize the security analysis for your needs

## Custom Scanning Configuration

Configure custom scanning and false positive filtering instructions — see the [`docs/`](docs/) folder for details.

## Testing

```bash
# Run all Python tests
pytest claudecode -v --cov=claudecode --cov-report=term-missing

# Run JavaScript tests
cd scripts && bun install && bun test
```

## Differences from the GitHub Action Version

This is a Bitbucket adaptation of the original [claude-code-security-review](https://github.com/anthropics/claude-code-security-review) GitHub Action. Key differences:

| | GitHub Action | Bitbucket Pipelines |
|---|---|---|
| **API** | GitHub REST API + `gh` CLI | Bitbucket Cloud API v2.0 |
| **Auth** | `GITHUB_TOKEN` (auto-provided) | `BITBUCKET_TOKEN` (manual setup) |
| **Subscription auth** | Supported via GitHub App (`max_subscription: true`) | Not supported — API key required |
| **PR comments** | GitHub review comments with reactions | Bitbucket inline comments |
| **Deduplication** | Cache-based marker files | Comment-based duplicate detection |
| **Config** | `action.yml` composite action | `bitbucket-pipelines.yml` |

## Support

For issues or questions, open an issue in this repository.

## License

MIT License - see [LICENSE](LICENSE) file for details.
