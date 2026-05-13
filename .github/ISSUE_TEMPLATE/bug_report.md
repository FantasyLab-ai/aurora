---
name: Bug Report
about: Report a bug or unexpected behavior
title: '[BUG] '
labels: bug
assignees: ''
---

## Description

A clear description of what the bug is.

## Reproduction Steps

1. Load dataset: [dataset name or description; attach if possible]
2. Set tier: [AUTO / QUICK / STANDARD / FULL]
3. Click / call: [specific action — UI click, SDK call, MCP tool, contract trigger]
4. Observe: [what happened]

## Expected Behavior

What you expected to happen.

## Actual Behavior

What actually happened. Include the exact error message if any.

## Environment

- Aurora version: [e.g., 1.1.0]
- Surface affected: [ ] Web Studio  [ ] Python SDK  [ ] MCP server  [ ] Decision Contracts  [ ] Research Kit
- OS: [macOS / Windows / Linux + version]
- Python version: [output of `python --version`]
- Browser (if Web Studio): [Chrome / Firefox / Safari + version]
- Dataset size: [rows × columns, file size]

## Logs

Paste any relevant error messages from the server console or browser console here. Wrap in triple backticks for formatting.

```
[paste log here]
```

## Aurora Bundle

If a bundle was produced (or could be), include its `content_hash` from `bundle.integrity.content_hash`. This helps identify the exact run conditions.

```
content_hash: ...
fabricated_count: ...
```

## Screenshots / GIFs

If applicable, add screenshots showing the issue.

## Additional Context

Anything else that might help.
