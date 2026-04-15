#!/usr/bin/env node

/**
 * Script to comment on Bitbucket PRs with security findings from ClaudeCode.
 *
 * Uses Bitbucket Cloud REST API v2.0 directly (no CLI dependency).
 *
 * Required environment variables:
 *   BITBUCKET_WORKSPACE  - Bitbucket workspace slug
 *   BITBUCKET_REPO_SLUG  - Repository slug
 *   BITBUCKET_PR_ID      - Pull request ID
 *   BITBUCKET_TOKEN      - Auth token (username:app_password or bearer token)
 */

const fs = require('fs');

const API_BASE = 'https://api.bitbucket.org/2.0';

const context = {
  workspace: process.env.BITBUCKET_WORKSPACE || '',
  repo_slug: process.env.BITBUCKET_REPO_SLUG || '',
  pr_id: parseInt(process.env.BITBUCKET_PR_ID || '0', 10),
};

/**
 * Build Authorization header from BITBUCKET_TOKEN.
 */
function getAuthHeader() {
  const token = process.env.BITBUCKET_TOKEN || '';
  if (!token) {
    throw new Error('BITBUCKET_TOKEN environment variable is required');
  }
  if (token.includes(':')) {
    const encoded = Buffer.from(token).toString('base64');
    return `Basic ${encoded}`;
  }
  return `Bearer ${token}`;
}

/**
 * Call the Bitbucket REST API.
 */
async function bitbucketApi(endpoint, method = 'GET', data = null) {
  const url = endpoint.startsWith('http') ? endpoint : `${API_BASE}${endpoint}`;

  const headers = {
    Authorization: getAuthHeader(),
    Accept: 'application/json',
  };

  const options = { method, headers };

  if (data) {
    headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(data);
  }

  const response = await fetch(url, options);

  if (!response.ok) {
    const body = await response.text();
    throw new Error(`Bitbucket API ${method} ${url} failed (${response.status}): ${body}`);
  }

  const text = await response.text();
  return text ? JSON.parse(text) : null;
}

/**
 * Fetch all PR comments (handles pagination).
 */
async function getAllPrComments() {
  const comments = [];
  let url = `/repositories/${context.workspace}/${context.repo_slug}/pullrequests/${context.pr_id}/comments?pagelen=100`;

  while (url) {
    const data = await bitbucketApi(url);
    if (data && data.values) {
      comments.push(...data.values);
    }
    url = data && data.next ? data.next : null;
  }

  return comments;
}

/**
 * Fetch diffstat to know which files are in the PR.
 */
async function getDiffstat() {
  const files = [];
  let url = `/repositories/${context.workspace}/${context.repo_slug}/pullrequests/${context.pr_id}/diffstat?pagelen=100`;

  while (url) {
    const data = await bitbucketApi(url);
    if (data && data.values) {
      for (const entry of data.values) {
        const newFile = entry.new;
        const oldFile = entry.old;
        const path = (newFile || oldFile || {}).path || '';
        files.push(path);
      }
    }
    url = data && data.next ? data.next : null;
  }

  return new Set(files);
}

async function run() {
  try {
    if (!context.workspace || !context.repo_slug || !context.pr_id) {
      console.log('Missing Bitbucket context (BITBUCKET_WORKSPACE, BITBUCKET_REPO_SLUG, or BITBUCKET_PR_ID)');
      return;
    }

    // Read findings
    let newFindings = [];
    try {
      const findingsData = fs.readFileSync('findings.json', 'utf8');
      newFindings = JSON.parse(findingsData);
    } catch (e) {
      console.log('Could not read findings file');
      return;
    }

    if (newFindings.length === 0) {
      return;
    }

    // Check if comments should be silenced
    if (process.env.SILENCE_CLAUDECODE_COMMENTS === 'true') {
      console.log(`ClaudeCode comments silenced - excluding ${newFindings.length} findings from comments`);
      return;
    }

    // Get files in the PR diff
    const diffFiles = await getDiffstat();

    // Check for existing security comments to avoid duplicates
    const existingComments = await getAllPrComments();
    const hasExistingSecurityComments = existingComments.some(
      c => c.content && c.content.raw && c.content.raw.includes('**Security Issue:')
    );

    if (hasExistingSecurityComments) {
      console.log('Found existing security comments, skipping to avoid duplicates');
      return;
    }

    // Post comments for each finding
    let posted = 0;

    for (const finding of newFindings) {
      const file = finding.file || finding.path;
      const line = finding.line || (finding.start && finding.start.line) || 1;
      const message = finding.description || (finding.extra && finding.extra.message) || 'Security vulnerability detected';
      const severity = finding.severity || 'HIGH';
      const category = finding.category || 'security_issue';

      // Build comment body (Markdown)
      let body = `🤖 **Security Issue: ${message}**\n\n`;
      body += `**Severity:** ${severity}\n`;
      body += `**Category:** ${category}\n`;
      body += `**Tool:** ClaudeCode AI Security Analysis\n`;

      if (finding.exploit_scenario || (finding.extra && finding.extra.metadata && finding.extra.metadata.exploit_scenario)) {
        const scenario = finding.exploit_scenario || finding.extra.metadata.exploit_scenario;
        body += `\n**Exploit Scenario:** ${scenario}\n`;
      }

      if (finding.recommendation || (finding.extra && finding.extra.metadata && finding.extra.metadata.recommendation)) {
        const rec = finding.recommendation || finding.extra.metadata.recommendation;
        body += `\n**Recommendation:** ${rec}\n`;
      }

      const endpoint = `/repositories/${context.workspace}/${context.repo_slug}/pullrequests/${context.pr_id}/comments`;

      // Try inline comment if the file is part of the PR
      if (file && diffFiles.has(file)) {
        try {
          await bitbucketApi(endpoint, 'POST', {
            content: { raw: body },
            inline: {
              path: file,
              to: line,
            },
          });
          posted++;
          console.log(`Posted inline comment on ${file}:${line}`);
          continue;
        } catch (inlineErr) {
          console.log(`Could not post inline comment on ${file}:${line} - ${inlineErr.message}`);
          // Fall through to general comment
        }
      }

      // Fallback: post as general PR comment
      try {
        const fallbackBody = `🤖 **Security Issue in \`${file || 'unknown'}:${line}\`:** ${message}\n\n`
          + `**Severity:** ${severity}\n`
          + `**Category:** ${category}\n`
          + `**Tool:** ClaudeCode AI Security Analysis\n`
          + (finding.exploit_scenario ? `\n**Exploit Scenario:** ${finding.exploit_scenario}\n` : '')
          + (finding.recommendation ? `\n**Recommendation:** ${finding.recommendation}\n` : '');

        await bitbucketApi(endpoint, 'POST', {
          content: { raw: fallbackBody },
        });
        posted++;
        console.log(`Posted general comment for ${file || 'unknown'}:${line}`);
      } catch (commentErr) {
        console.error(`Failed to post comment for ${file}:${line}:`, commentErr.message);
      }
    }

    console.log(`Posted ${posted} of ${newFindings.length} security comments`);
  } catch (error) {
    console.error('Failed to comment on PR:', error);
    process.exit(1);
  }
}

run();
