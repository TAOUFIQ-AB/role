#!/usr/bin/env python3
# ─────────────────────────────────────────────────────────────────────────────
# config.py — Central configuration
# All values are read from environment variables; hard defaults make the agent
# runnable without any env vars for local testing.
# ─────────────────────────────────────────────────────────────────────────────

import logging
import os
from pathlib import Path
from typing import List


def _env_int(name: str, default: int, minimum: int | None = None) -> int:
    """Read an integer environment variable without making config import brittle."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        value = default
    else:
        try:
            value = int(raw)
        except ValueError:
            logging.getLogger("Config").warning(
                "Invalid integer %s=%r; using default %s", name, raw, default
            )
            value = default
    if minimum is not None and value < minimum:
        logging.getLogger("Config").warning(
            "%s=%s is below minimum %s; using %s", name, value, minimum, minimum
        )
        value = minimum
    return value


class Config:
    # ── Instagram ──────────────────────────────────────────────────────────────
    INSTAGRAM_SESSION_COOKIES: str = os.environ.get("INSTAGRAM_SESSION_COOKIES", "")
    INSTAGRAM_REELS_URL: str = "https://www.instagram.com/reels/"

    # Search-first discovery. The hunter intentionally searches Instagram for
    # GTA 6 content instead of relying on whatever happens to be in the Reels feed.
    INSTAGRAM_SEARCH_URL: str = "https://www.instagram.com/explore/search/keyword/"
    INSTAGRAM_SEARCH_QUERIES: List[str] = [
        q.strip()
        for q in os.environ.get(
            "INSTAGRAM_SEARCH_QUERIES",
            "GTA 6,GTA VI,Grand Theft Auto VI,GTA 6 trailer 2,GTA 6 Rockstar,GTA 6 Lucia,GTA 6 Jason,GTA 6 Vice City,GTA 6 Leonida,GTA 6 gameplay,GTA 6 edit",
        ).split(",")
        if q.strip()
    ]
    SEARCH_SCROLLS_PER_QUERY: int = _env_int("SEARCH_SCROLLS_PER_QUERY", 10, 1)
    SEARCH_MAX_PER_QUERY: int = _env_int("SEARCH_MAX_PER_QUERY", 9, 1)
    SEARCH_POOL_MULTIPLIER: int = _env_int("SEARCH_POOL_MULTIPLIER", 3, 1)
    SEARCH_MIN_PER_QUERY: int = _env_int("SEARCH_MIN_PER_QUERY", 2, 0)

    # ── Viral thresholds ───────────────────────────────────────────────────────
    MIN_VIEWS: int = _env_int("MIN_VIEWS", 0, 0)
    MIN_LIKES: int = _env_int("MIN_LIKES", 0, 0)

    # ── Gemini vision ──────────────────────────────────────────────────────────
    GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "")
    GEMINI_MODEL: str = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
    GEMINI_MAX_DIM: int = _env_int("GEMINI_MAX_DIM", 720, 128)
    # How many times to retry a transient Gemini error before failing closed
    GEMINI_RETRIES: int = _env_int("GEMINI_RETRIES", 2, 0)

    # ── Gemini Web fallback (browser-based, no API key needed) ────────────────
    # Paste Google account cookies (JSON array or semicolon-separated) so the
    # agent can query gemini.google.com directly when the API key is absent or
    # quota-exhausted.
    GEMINI_COOKIES: str = os.environ.get("GEMINI_COOKIES", "")
    # Enable Gemini Web as a vision provider when API key is unavailable
    GEMINI_WEB_ENABLED: bool = os.environ.get("GEMINI_WEB_ENABLED", "true").strip().lower() == "true"
    # ── Text / hashtag providers ───────────────────────────────────────────────
    # Provider order is automatic by default: first configured provider wins.
    AI_PROVIDER_ORDER: List[str] = [
        p.strip().lower()
        for p in os.environ.get("AI_PROVIDER_ORDER", "gemini,groq,openrouter")
        .split(",")
        if p.strip()
    ]
    GROQ_API_KEY: str = os.environ.get("GROQ_API_KEY", "")
    # Current production Groq models. "auto" tries the stronger model first,
    # then the smaller/faster production fallback. Override GROQ_MODEL if needed.
    GROQ_MODEL: str = os.environ.get("GROQ_MODEL", "auto")
    GROQ_MODEL_CASCADE: List[str] = [
        m.strip()
        for m in os.environ.get(
            "GROQ_MODEL_CASCADE",
            "openai/gpt-oss-120b,openai/gpt-oss-20b",
        ).split(",")
        if m.strip()
    ]
    OPENROUTER_API_KEY: str = os.environ.get("OPENROUTER_API_KEY", "")
    # "auto" = OpenRouter's maintained free router, avoiding stale hard-coded slugs.
    # Override OPENROUTER_MODEL or OPENROUTER_MODEL_CASCADE for a fixed model.
    OPENROUTER_MODEL: str = os.environ.get("OPENROUTER_MODEL", "auto")
    OPENROUTER_MODEL_CASCADE: List[str] = [
        m.strip()
        for m in os.environ.get(
            "OPENROUTER_MODEL_CASCADE",
            "openrouter/free",
        ).split(",")
        if m.strip()
    ]
    OPENROUTER_APP_NAME: str = os.environ.get("OPENROUTER_APP_NAME", "Reels Hunter")
    OPENROUTER_SITE_URL: str = os.environ.get("OPENROUTER_SITE_URL", "").strip()
    
    # ── Gemini Fallback Mode (when API quota/limit is hit) ────────────────────
    # When True, if Gemini API fails due to quota/limits, fall back to using 
    # views/likes metrics to determine quality instead of rejecting the reel
    ENABLE_GEMINI_FALLBACK: bool = os.environ.get("ENABLE_GEMINI_FALLBACK", "true").strip().lower() == "true"
    # Minimum views required when falling back (if Gemini unavailable)
    FALLBACK_MIN_VIEWS: int = _env_int("FALLBACK_MIN_VIEWS", 500000, 0)
    # Minimum likes required when falling back (if Gemini unavailable)
    FALLBACK_MIN_LIKES: int = _env_int("FALLBACK_MIN_LIKES", 250000, 0)

    # ── Telegram ───────────────────────────────────────────────────────────────
    TELEGRAM_BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.environ.get("TELEGRAM_CHAT_ID", "")
    TELEGRAM_MAX_VIDEO_MB: int = _env_int("TELEGRAM_MAX_VIDEO_MB", 49, 1)

    # ── TikTok ────────────────────────────────────────────────────────────────
    # Set TIKTOK_ENABLED=true to activate auto-posting after each Telegram send.
    TIKTOK_ENABLED: bool = os.environ.get("TIKTOK_ENABLED", "false").strip().lower() == "true"

    # Auth mode: "cookies" (default) or "session"
    #   cookies  — point TIKTOK_COOKIES_FILE at a Netscape cookies.txt you
    #              exported from a logged-in browser session.
    #   session  — paste the raw `sessionid` cookie value (or full cookie
    #              string) into TIKTOK_SESSION_COOKIES.
    TIKTOK_AUTH_MODE: str = os.environ.get("TIKTOK_AUTH_MODE", "cookies").strip().lower()
    TIKTOK_COOKIES_FILE: str = os.path.expandvars(os.path.expanduser(
        os.environ.get("TIKTOK_COOKIES_FILE", "~/.secrets/tiktok_cookies.txt")
    ))
    # Accepts either a bare sessionid value ("abc123") or a full cookie string
    # ("sessionid=abc123; tt_csrf_token=xyz; ...") — both are handled.
    TIKTOK_SESSION_COOKIES: str = os.environ.get("TIKTOK_SESSION_COOKIES", "")
    # Backwards-compatible alias for older docs/scripts.
    TIKTOK_SESSION_ID: str = os.environ.get("TIKTOK_SESSION_ID", "")
    # Manual login fallback — used when cookies are absent/expired
    TIKTOK_EMAIL: str = os.environ.get("TIKTOK_EMAIL", "")
    TIKTOK_PASSWORD: str = os.environ.get("TIKTOK_PASSWORD", "")

    # TikTok browser headless mode — defaults False (visible) for local use;
    # set TIKTOK_HEADLESS=true in CI/GitHub Actions to avoid display errors.
    TIKTOK_HEADLESS: bool = os.environ.get("TIKTOK_HEADLESS", "false").strip().lower() == "true"

    # Retry budget for failed uploads within a single run
    TIKTOK_MAX_RETRIES: int = _env_int("TIKTOK_MAX_RETRIES", 2, 0)
    # Hard guard for uploads that hang on the page automation layer
    TIKTOK_UPLOAD_TIMEOUT_SECONDS: int = _env_int("TIKTOK_UPLOAD_TIMEOUT_SECONDS", 240, 30)

    # Caption template — use {url}, {views}, {likes}, {tags} placeholders.
    # Leave empty for the default short-caption mode.
    TIKTOK_CAPTION_TEMPLATE: str = os.environ.get("TIKTOK_CAPTION_TEMPLATE", "")

    # Hashtags appended to every TikTok post (comma-separated)
    TIKTOK_HASHTAGS: List[str] = [
        h.strip().lstrip("#")
        for h in os.environ.get(
            "TIKTOK_HASHTAGS",
            "gta6,gtavi,grandtheftauto6,rockstargames,vicecity,fyp,viral",
        ).split(",")
        if h.strip()
    ]

    # ── Backblaze B2 storage ───────────────────────────────────────────────────
    # Set these to persist downloaded reels beyond the GitHub Actions run.
    B2_APPLICATION_KEY_ID: str = os.environ.get("B2_APPLICATION_KEY_ID", "")
    B2_APPLICATION_KEY:    str = os.environ.get("B2_APPLICATION_KEY", "")
    B2_BUCKET_NAME:        str = os.environ.get("B2_BUCKET_NAME", "reels-hunter")

    # ── Database ───────────────────────────────────────────────────────────────
    DB_PATH: str = os.environ.get("DB_PATH", "history.db")

    # ── Runtime limits ─────────────────────────────────────────────────────────
    MAX_RUNTIME_SECONDS: int = _env_int("MAX_RUNTIME_SECONDS", 480, 60)
    SHUTDOWN_BUFFER_SECONDS: int = _env_int("SHUTDOWN_BUFFER_SECONDS", 45, 0)
    TARGET_REELS_SCAN: int = _env_int("TARGET_REELS_SCAN", 35, 1)
    MAX_QUALIFIED_SEND: int = _env_int("MAX_QUALIFIED_SEND", 5, 1)
    ONE_SHOT: bool = os.environ.get("ONE_SHOT", "false").strip().lower() == "true"

    # ── Retry / queue ──────────────────────────────────────────────────────────
    # How many times to retry a failed Telegram send (persisted across runs)
    MAX_UPLOAD_ATTEMPTS: int = _env_int("MAX_UPLOAD_ATTEMPTS", 3, 1)
    # How many times to retry a failed reel download within a single run
    MAX_DOWNLOAD_ATTEMPTS: int = _env_int("MAX_DOWNLOAD_ATTEMPTS", 2, 1)

    # ── Paths ──────────────────────────────────────────────────────────────────
    DOWNLOAD_DIR: Path = Path(os.environ.get("DOWNLOAD_DIR", "/tmp/reels_downloads"))
    SCREENSHOT_DIR: Path = Path(os.environ.get("SCREENSHOT_DIR", "/tmp/reels_screenshots"))
    COOKIES_FILE: Path = Path("/tmp/ig_cookies.txt")


    # ── Proxy (Webshare.io) ────────────────────────────────────────────────────
    # Set WEBSHARE_API_KEY to enable rotating residential/datacenter proxies.
    # Leave empty to browse directly — the system-level VPN (Japan or otherwise)
    # will be used by default since Playwright inherits OS network when no
    # explicit proxy is configured.
    #
    # IMPORTANT — VPN coexistence:
    #   When WEBSHARE_ENABLED=true (default when API key is present), Playwright
    #   routes browser traffic through Webshare, which BYPASSES any system-level
    #   VPN for that browser context.  Non-browser traffic (requests, yt-dlp,
    #   Telegram) is unaffected and still goes through the VPN.
    #
    #   To keep using the system VPN for the browser:
    #     • Leave WEBSHARE_API_KEY empty, OR
    #     • Set WEBSHARE_ENABLED=false explicitly
    #
    # WEBSHARE_PROXY_MODE must match your Webshare plan:
    #   "direct"   — datacenter / rotating proxies  (default, most plans)
    #   "backbone" — residential backbone proxies   (premium plans)
    WEBSHARE_API_KEY:    str  = os.environ.get("WEBSHARE_API_KEY",   "")
    WEBSHARE_PROXY_MODE: str  = os.environ.get("WEBSHARE_PROXY_MODE", "direct")
    # Explicit on/off override — defaults True when a key is present.
    # Set WEBSHARE_ENABLED=false to keep the system VPN active even if a key is set.
    WEBSHARE_ENABLED: bool = (
        os.environ.get("WEBSHARE_ENABLED", "").strip().lower() == "true"
        if os.environ.get("WEBSHARE_ENABLED", "").strip()
        else bool(os.environ.get("WEBSHARE_API_KEY", ""))
    )

    # ── Browser ────────────────────────────────────────────────────────────────
    HEADLESS: bool = os.environ.get("PLAYWRIGHT_HEADLESS", "false").strip().lower() == "true"
    VIEWPORT_W: int = _env_int("VIEWPORT_W", 430, 320)
    VIEWPORT_H: int = _env_int("VIEWPORT_H", 932, 480)

    # Persistent Chrome profile directory.
    # When set, the browser reuses the same IndexedDB, localStorage, service
    # workers, and profile history across sessions — Gemini and TikTok both
    # trust long-lived profiles far more than fresh ephemeral contexts.
    # Example: CHROME_PROFILE_DIR=/data/chrome-profile
    # Leave empty to use the old ephemeral new_context() behaviour.
    CHROME_PROFILE_DIR: str = os.environ.get("CHROME_PROFILE_DIR", "")

    # ── Provider quota backoff ─────────────────────────────────────────────────
    # Base cooldown (seconds) after the first quota hit.  Doubles on every
    # subsequent failure up to PROVIDER_COOLDOWN_MAX (exponential backoff).
    # Formula: min(MAX, INITIAL * 2^(failure_count - 1))
    PROVIDER_COOLDOWN_INITIAL: int = _env_int("PROVIDER_COOLDOWN_INITIAL", 300, 1)
    PROVIDER_COOLDOWN_MAX: int     = _env_int("PROVIDER_COOLDOWN_MAX", 3600, 1)

    # Leave empty by default so Chromium reports a self-consistent native UA.
    # Set BROWSER_USER_AGENT only when a specific UA override is required.
    BROWSER_USER_AGENT: str = os.environ.get("BROWSER_USER_AGENT", "").strip()
    # Optional Playwright browser channel (for example "chrome"). Empty uses
    # Playwright's version-matched bundled Chromium.
    BROWSER_CHANNEL: str = os.environ.get("PLAYWRIGHT_BROWSER_CHANNEL", "").strip()
    BROWSER_TIMEZONE: str = os.environ.get("BROWSER_TIMEZONE", "UTC").strip() or "UTC"

    # ── Target accounts whitelist (Faceless / Edit accounts) ──────────────────
    # Add Instagram usernames you want the bot to scrape — one per line, no @.
    # Set via the USERS_ATTACK env-var (newline- or comma-separated) or add
    # them directly in the list below.  Leave empty to use the normal feed.
    USERS_ATTACK: List[str] = [
        u.strip().lstrip("@")
        for u in os.environ.get("USERS_ATTACK", "").replace(",", "\n").splitlines()
        if u.strip()
    ]

    # ── Caption / hashtag content filter ──────────────────────────────────────
    # Words found in captions or hashtags that immediately disqualify a reel.
    CAPTION_BLACKLIST: List[str] = [
        w.strip()
        for w in os.environ.get(
            "CAPTION_BLACKLIST",
            "POV,Vlog,Day in my life,OOTD,Outfit of the day,GRWM,"
            "Get ready with me,Selfie,My girlfriend,My boyfriend,Travel vlog,"
            "storytime,come with me,day with me,morning routine,night routine",
        ).split(",")
        if w.strip()
    ]

    # Words found in captions or hashtags that mark a reel as a desirable Edit.
    CAPTION_WHITELIST: List[str] = [
        w.strip()
        for w in os.environ.get(
            "CAPTION_WHITELIST",
            "GTA 6,GTA6,GTA VI,Grand Theft Auto VI,Grand Theft Auto 6,"
            "Lucia,Jason,Leonida,Vice City,Rockstar Games,GTA 6 trailer,"
            "GTA 6 gameplay,GTA 6 edit",
        ).split(",")
        if w.strip()
    ]

    # ── Vision / black-bar detection ───────────────────────────────────────────
    BLACK_THRESHOLD: int = _env_int("BLACK_THRESHOLD", 28, 0)
    BLACK_BAR_RATIO: float = float(os.environ.get("BLACK_BAR_RATIO", "0.82"))
    BORDER_SAMPLE_PCT: float = float(os.environ.get("BORDER_SAMPLE_PCT", "0.05"))

    @classmethod
    def summary(cls) -> str:
        users_str = ", ".join(cls.USERS_ATTACK) if cls.USERS_ATTACK else "(feed mode)"
        lines = [
            "+- Config ---------------------------------------------------",
            f"|  Max runtime        : {cls.MAX_RUNTIME_SECONDS}s (buffer {cls.SHUTDOWN_BUFFER_SECONDS}s)",
            f"|  Min views          : {cls.MIN_VIEWS:,}",
            f"|  Min likes          : {cls.MIN_LIKES:,}",
            f"|  Target scan count  : {cls.TARGET_REELS_SCAN}",
            f"|  Max send count     : {cls.MAX_QUALIFIED_SEND}",
            f"|  Max upload retry   : {cls.MAX_UPLOAD_ATTEMPTS}",
            f"|  Headless           : {cls.HEADLESS}",
            f"|  DB path            : {cls.DB_PATH}",
            f"|  Gemini model       : {cls.GEMINI_MODEL}",
            f"|  Gemini enabled     : {bool(cls.GEMINI_API_KEY)}",
            f"|  Gemini Web enabled : {cls.GEMINI_WEB_ENABLED and bool(cls.GEMINI_COOKIES)}",
            f"|  Groq model         : {cls.GROQ_MODEL}",
            f"|  Groq enabled       : {bool(cls.GROQ_API_KEY)}",
            f"|  OpenRouter model   : {cls.OPENROUTER_MODEL}",
            f"|  OpenRouter enabled : {bool(cls.OPENROUTER_API_KEY)}",
            f"|  Provider order     : {', '.join(cls.AI_PROVIDER_ORDER)}",
            f"|  Gemini fallback    : {cls.ENABLE_GEMINI_FALLBACK}",
            f"|  Fallback min views : {cls.FALLBACK_MIN_VIEWS:,}",
            f"|  Fallback min likes : {cls.FALLBACK_MIN_LIKES:,}",
            f"|  Telegram enabled   : {bool(cls.TELEGRAM_BOT_TOKEN and cls.TELEGRAM_CHAT_ID)}",
            f"|  TikTok enabled     : {cls.TIKTOK_ENABLED}",
            f"|  TikTok auth mode   : {cls.TIKTOK_AUTH_MODE if cls.TIKTOK_ENABLED else chr(110)+chr(47)+chr(97)}",
            f"|  Cookies set        : {bool(cls.INSTAGRAM_SESSION_COOKIES)}",
            f"|  Proxy enabled      : {cls.WEBSHARE_ENABLED and bool(cls.WEBSHARE_API_KEY)} (mode={cls.WEBSHARE_PROXY_MODE})",
            f"|  Chrome profile     : {cls.CHROME_PROFILE_DIR or chr(40)+chr(101)+chr(112)+chr(104)+chr(101)+chr(109)+chr(101)+chr(114)+chr(97)+chr(108)+chr(41)}",
            f"|  Cooldown initial   : {cls.PROVIDER_COOLDOWN_INITIAL}s  max={cls.PROVIDER_COOLDOWN_MAX}s",
            f"|  Target accounts    : {users_str}",
            f"|  Search queries     : {', '.join(cls.INSTAGRAM_SEARCH_QUERIES)}",
            f"|  Search scrolls     : {cls.SEARCH_SCROLLS_PER_QUERY}/query",
            f"|  Search max/query   : {cls.SEARCH_MAX_PER_QUERY}",
            f"|  Search pool x      : {cls.SEARCH_POOL_MULTIPLIER}",
            f"|  Search min/query   : {cls.SEARCH_MIN_PER_QUERY}",
            f"|  Caption blacklist  : {len(cls.CAPTION_BLACKLIST)} words",
            f"|  Caption whitelist  : {len(cls.CAPTION_WHITELIST)} words",
            "+------------------------------------------------------------",
        ]
        return "\n".join(lines)
