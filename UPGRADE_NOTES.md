# September 2026 hardening pass

This repository was audited and upgraded for GitHub Actions reliability.

## Fixed defects

- Fixed retry classification being evaluated before the failure type was stored.
- Fixed retry attempts being incremented twice during requeue.
- Fixed initial work being both queued and processed immediately, which could duplicate processing.
- Fixed an undefined provider cooldown constant in the AI router.
- Removed a broken/nonexistent Gemini-Web import path from the generic AI router.
- Restored TikTok cookie-file creation in GitHub Actions.
- Fixed Webshare's explicit enable/disable behavior.
- Removed stale hard-coded browser client hints and unsafe legacy Chromium flags.
- Removed deprecated UTC timestamp construction in the SQLite layer.
- Fixed invalid escape-sequence warnings in injected JavaScript strings.
- Prevented the TikTok secret updater from silently reusing generic GitHub tokens.

## Updated platform/runtime

- Ubuntu 24.04 GitHub-hosted runner.
- Python 3.12.
- Playwright 1.63.0.
- `google-genai` 2.24.0.
- Gemini 3.8 Flash default.
- Current Groq production fallback model IDs.
- OpenRouter `openrouter/free` maintained free router.
- Current Xpra Ubuntu Noble repository.
- Modern official GitHub Action majors.

## Added safeguards

- Separate CI workflow with warning-free compilation and unit tests.
- Workflow input validation.
- Concurrency control so scheduled runs do not overlap.
- Password-only Xpra remote desktop and manual opt-in on Actions.
- Safer scheduled headless mode.
- SQLite checkpointing plus persistent cache restore/save.
- Diagnostic artifacts and Playwright traces.
- Cookie validation before production execution.
- Dependabot for pip, Actions, and Docker.
