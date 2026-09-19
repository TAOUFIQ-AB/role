# Reels AI Hunter

A Python automation pipeline for discovering Instagram Reels, filtering them by metrics and visual checks, downloading qualified media, delivering results to Telegram, and optionally posting successful deliveries to TikTok.

The repository is optimized for **GitHub Actions**. Scheduled runs are headless; an Xpra HTML5 desktop is available only when manually requested for debugging.

## What was modernized

- GitHub Actions: Ubuntu 24.04, Python 3.12, current official action majors, concurrency protection, dependency/browser caching, persistent SQLite state, diagnostics, and CI tests.
- Browser runtime: Playwright 1.63.0, supported Chrome channel on GitHub-hosted runners, bundled version-matched Chromium in Docker, native browser client hints, and safer launch flags.
- AI routing: current `google-genai` SDK, Gemini 3.8 Flash default, quota-aware fallback, current Groq production fallbacks, and OpenRouter's maintained `openrouter/free` router.
- Vision fallback: Gemini API → optional Gemini Web → configured LLaVA endpoint → explicit metric fallback.
- Reliability: corrected retry accounting, duplicate queue processing, SQLite/WAL handling, cookie validation, provider cooldowns, and configuration defaults.
- Security: no unauthenticated Xpra desktop, no automatic use of generic GitHub tokens for secret mutation, no committed runtime cookies, and read-only workflow permissions by default.

## Repository layout

```text
app/                         Python application
.github/workflows/ci.yml     compile + unit-test CI
.github/workflows/reels_agent.yml
                             scheduled/manual production workflow
.github/scripts/             cookie/secret validation helpers
tests/                       regression tests
Dockerfile                   local/container runtime
entrypoint.sh                secure Xpra + agent launcher
requirements.txt             pinned runtime dependencies
requirements-dev.txt         test/tooling dependencies
```

## GitHub Actions setup

### 1. Add the required secret

In **Settings → Secrets and variables → Actions**, create:

| Secret | Required | Purpose |
|---|---:|---|
| `INSTAGRAM_SESSION_COOKIES` | Yes | Authenticated Instagram session cookies. JSON export or `name=value; ...` format is accepted. |
| `GEMINI_API_KEY` | Recommended | Primary Gemini vision/text provider. |
| `TELEGRAM_BOT_TOKEN` | Recommended | Telegram delivery/alerts. |
| `TELEGRAM_CHAT_ID` | Recommended | Destination chat/channel. |
| `GROQ_API_KEY` | Optional | Text fallback. |
| `OPENROUTER_API_KEY` | Optional | Text fallback through OpenRouter. |
| `GEMINI_COOKIES` | Optional | Gemini Web browser fallback cookies. |
| `OLLAMA_BASE_URL` | Optional | LLaVA/OpenAI-compatible vision endpoint used as a fallback. |
| `HF_TOKEN` | Optional | Token for a configured hosted vision endpoint, when required by that endpoint. |
| `TIKTOK_COOKIES` | Optional | TikTok uploader cookies. |
| `TIKTOK_SESSION_COOKIES` | Optional | Alternate TikTok session cookie source. |
| `TIKTOK_EMAIL` / `TIKTOK_PASSWORD` | Optional | TikTok login fallback. |
| `TIKTOK_SECRET_UPDATE_TOKEN` | Optional | Explicit opt-in token allowed to update the repository's TikTok cookie secret. Do not reuse a broad generic token. |
| `B2_APPLICATION_KEY_ID` / `B2_APPLICATION_KEY` | Optional | Backblaze B2 upload support. |
| `WEBSHARE_API_KEY` | Optional | Webshare proxy support. |
| `XPRA_PASSWORD` | Debug only | Required only when a manual run enables the remote desktop. |

`INSTAGRAM_SESSION_COOKIES` is validated before the agent starts. Missing optional integrations do not make the workflow fail.

### 2. Optional repository variables

Set these under **Settings → Secrets and variables → Actions → Variables** only when you need to override defaults:

| Variable | Example | Purpose |
|---|---|---|
| `USERS_ATTACK` | `account1,account2` | Target specific accounts. Leave empty for feed-based discovery. |
| `CAPTION_BLACKLIST` | `word1,word2` | Skip matching captions. |
| `CAPTION_WHITELIST` | `topic1,topic2` | Prefer/require matching caption terms according to app logic. |
| `OLLAMA_MODEL` | `llava` | Vision fallback model name. |
| `B2_BUCKET_NAME` | `reels-hunter` | Backblaze bucket. |
| `WEBSHARE_ENABLED` | `true` | Enables Webshare only when an API key is also present. |
| `WEBSHARE_PROXY_MODE` | `direct` | Proxy mode used by the application. |

### 3. Run it

- **Scheduled:** every 4 hours, headless, TikTok posting disabled by default.
- **Manual:** open **Actions → Reels AI Hunter → Run workflow** to override thresholds, enable TikTok, or enable the debug desktop.
- **Desktop debugging:** set `XPRA_PASSWORD`, then enable `enable_desktop`. The job attempts a temporary `localhost.run` tunnel and reports the URL in the job summary. The desktop is password protected.

The production job has a 55-minute Actions timeout and the agent also has its own runtime ceiling/graceful-shutdown buffer.

## Instagram cookie formats

Semicolon format:

```text
sessionid=...; csrftoken=...; ds_user_id=...
```

A browser-exported JSON cookie array is also accepted. Never commit cookie files or cookie values to the repository.

## Processing flow

```text
Discover Reel
   ↓
Deduplicate against SQLite
   ↓
Extract views / likes / caption
   ↓
Apply configured metric + caption filters
   ↓
Capture frame / screenshot
   ↓
Cheap local visual checks
   ↓
Gemini Vision
   ├─ model decision → keep/reject
   └─ provider unavailable
        ↓
     Gemini Web / LLaVA fallback
        ↓
     optional metric fallback only if AI providers are unavailable
   ↓
Download with yt-dlp
   ↓
Telegram delivery
   ↓
Optional TikTok / B2 follow-up
```

A real AI `FAILED` result is not converted into a pass by the engagement-metric fallback. The metric fallback is for provider unavailability/errors only.

## Important runtime defaults

| Environment variable | Default | Meaning |
|---|---:|---|
| `MIN_VIEWS` | `50000` in Actions | Minimum view threshold; `0` disables it. |
| `MIN_LIKES` | `0` | Minimum like threshold; `0` disables it. |
| `TARGET_REELS_SCAN` | `35` in Actions | Maximum discovery/scan target. |
| `MAX_QUALIFIED_SEND` | `5` in Actions | Maximum qualified deliveries per run. |
| `MAX_RUNTIME_SECONDS` | `2400` in Actions | Agent runtime ceiling. |
| `PLAYWRIGHT_HEADLESS` | `true` in scheduled Actions | Headless browser mode. |
| `GEMINI_MODEL` | `gemini-3.8-flash` | Primary Gemini model. |
| `AI_PROVIDER_ORDER` | `gemini,groq,openrouter` | Text-provider priority. |
| `GROQ_MODEL` | `auto` | Uses current production fallback cascade when `auto`. |
| `OPENROUTER_MODEL` | `auto` | Uses `openrouter/free` when `auto`. |
| `ENABLE_GEMINI_FALLBACK` | `true` | Allows the explicit engagement fallback if vision providers are unavailable. |
| `BROWSER_TIMEZONE` | `UTC` | Browser timezone. |
| `TIKTOK_ENABLED` | `false` in Actions | Posting is opt-in for manual runs. |

See `app/config.py` for every supported override.

## CI and local tests

The `CI` workflow runs on pushes and pull requests. Locally:

```bash
python -m pip install -r requirements.txt -r requirements-dev.txt
python -W error -m compileall -q app .github/scripts tests
python -m unittest discover -s tests -v
```

The regression tests currently cover retry accounting, database persistence/checkpoint behavior, browser cookie parsing, AI parsing/provider configuration, and workflow/repository invariants.

## Docker

The Docker image is based on the Playwright 1.63.0 Ubuntu Noble image and installs the current Xpra Noble repository.

```bash
docker build -t reels-hunter .
mkdir -p data

docker run --rm -it \
  -p 8080:8080 \
  -v "$(pwd)/data:/data" \
  -e DB_PATH=/data/history.db \
  -e XPRA_PASSWORD='choose-a-strong-password' \
  -e INSTAGRAM_SESSION_COOKIES='sessionid=...; csrftoken=...; ds_user_id=...' \
  -e GEMINI_API_KEY='...' \
  -e TELEGRAM_BOT_TOKEN='...' \
  -e TELEGRAM_CHAT_ID='...' \
  reels-hunter
```

Open `http://localhost:8080/` and authenticate with `XPRA_PASSWORD`. The container refuses to start the remotely accessible HTML5 desktop without an explicit password.

## Persistence and diagnostics

`history.db` uses SQLite/WAL. In GitHub Actions the database is restored from an Actions cache, checkpointed after every run, and saved again using a unique immutable cache key. Diagnostic logs/screenshots/traces are uploaded as short-retention artifacts even when the agent fails.

Downloaded videos left behind after a crash are uploaded as a separate short-retention artifact to make debugging/recovery easier.

## Maintenance

Dependabot is enabled for Python packages, GitHub Actions, and Docker base-image updates. Review dependency PRs rather than merging them blindly, because browser automation and uploader libraries can introduce behavior changes even in non-major releases.
