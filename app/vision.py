#!/usr/bin/env python3
# ─────────────────────────────────────────────────────────────────────────────
# vision.py — Visual quality / attribution filter
#
# Primary path: Gemini API via the current google-genai SDK.
# Fallbacks: shared-browser Gemini Web, then configured external vision.
# If all AI providers are unavailable, an optional engagement-metric fallback
# can be used; actual AI FAILED decisions are never overridden.
# ─────────────────────────────────────────────────────────────────────────────

import logging
import time
from io import BytesIO
from typing import List, Tuple, Optional

from PIL import Image

from ai_router import AIProviderRouter, parse_hashtags
from config import Config
from gemini_web_browser import GeminiWebBrowser


class VisionEvaluator:
    _GEMINI_PROMPT = (
        "You are a strict binary visual relevance + repost-safety filter for a GTA 6 content page.\n\n"
        "The supplied image may be a 3-frame contact sheet showing EARLY, MIDDLE, and LATE "
        "moments from the same reel from left to right. Judge the reel using all visible frames, "
        "not just one panel.\n\n"
        "OUTPUT 'PASSED' ONLY IF BOTH CONDITIONS ARE TRUE:\n"
        "1. The frame is clearly about GTA VI / GTA 6 / Grand Theft Auto VI. "
        "Strong evidence includes GTA VI branding, recognizable official trailer/game imagery, "
        "Lucia, Jason, Vice City/Leonida scenes, or unmistakable GTA VI-specific content.\n"
        "2. The frame is clean enough to repost: no platform watermark, no creator username/handle, "
        "and no text that identifies the original uploader/source.\n\n"
        "OUTPUT 'FAILED' IF ANY OF THESE APPLY:\n"
        "- It is GTA V, GTA Online, San Andreas, another GTA title, another game, or generic gaming footage.\n"
        "- The frame is a meme, reaction, unrelated real-life clip, streamer face-cam, or ambiguous content "
        "that cannot be confidently identified as GTA VI.\n"
        "- TikTok, Instagram, YouTube, or another platform logo/watermark is visible.\n"
        "- A creator handle/username or source-credit overlay is visible.\n\n"
        "IMPORTANT: Official Rockstar Games or GTA VI logos/branding are allowed and should NOT be treated "
        "as repost watermarks.\n\n"
        "CRITICAL: Reply with EXACTLY ONE WORD — 'PASSED' or 'FAILED'. Nothing else."
    )

    # Exceptions that indicate a transient Gemini error worth retrying
    _RETRYABLE_MESSAGES = (
        "quota", "rate", "503", "502", "timeout", "deadline", "unavailable",
        "resource_exhausted", "internal",
    )

    def __init__(self, gemini_api_key: str):
        self.log = logging.getLogger("VisionEvaluator")
        self._ai = AIProviderRouter()
        self.gemini_enabled = self._ai.gemini_ready
        if self.gemini_enabled:
            self.log.info(f"Gemini Vision ready — model={Config.GEMINI_MODEL}")
        else:
            self.log.warning("Gemini API key not set — will use Gemini Web Browser (visible) as fallback.")

        # Gemini Web reuses the agent's existing Playwright context/page, so it
        # works in both headless scheduled runs and optional desktop debug runs.
        self._gemini_web_browser: Optional[GeminiWebBrowser] = (
            GeminiWebBrowser(Config.GEMINI_COOKIES) if Config.GEMINI_WEB_ENABLED else None
        )
        if self._gemini_web_browser is not None:
            self.log.info("GeminiWebBrowser ready as quota/no-key fallback.")

        # Once the API quota is hit during a run, skip directly to web browser
        # for all subsequent reels instead of hammering the API on every check.
        self._api_quota_hit: bool = False

        # Will be set by agent after notifier is available
        self._notifier = None

    # ── Stage 1: local Pillow pixel analysis ──────────────────────────────────

    def _sample_strip(
        self, img: Image.Image, box: Tuple[int, int, int, int], step: int = 4
    ) -> List[Tuple[int, int, int]]:
        x0, y0, x1, y1 = box
        pixels: List[Tuple[int, int, int]] = []
        for y in range(y0, y1, max(1, step)):
            for x in range(x0, x1, max(1, step)):
                px = img.getpixel((x, y))
                pixels.append((px[0], px[1], px[2]))
        return pixels

    def _is_black_strip(self, pixels: List[Tuple[int, int, int]]) -> bool:
        if not pixels:
            return False
        thr = Config.BLACK_THRESHOLD
        ratio = Config.BLACK_BAR_RATIO
        black = sum(1 for r, g, b in pixels if r < thr and g < thr and b < thr)
        return (black / len(pixels)) >= ratio

    def check_aspect_ratio_local(self, screenshot_bytes: bytes) -> Tuple[bool, str]:
        try:
            img = Image.open(BytesIO(screenshot_bytes)).convert("RGB")
            w, h = img.size
            bh = max(2, int(h * Config.BORDER_SAMPLE_PCT))
            bw = max(2, int(w * Config.BORDER_SAMPLE_PCT))
            top_black    = self._is_black_strip(self._sample_strip(img, (0, 0, w, bh)))
            bottom_black = self._is_black_strip(self._sample_strip(img, (0, h - bh, w, h)))
            left_black   = self._is_black_strip(self._sample_strip(img, (0, 0, bw, h)))
            right_black  = self._is_black_strip(self._sample_strip(img, (w - bw, 0, w, h)))
            self.log.debug(
                f"Border: top={top_black} bottom={bottom_black} "
                f"left={left_black} right={right_black} ({w}x{h})"
            )
            if top_black and bottom_black:
                return False, "Letterbox bars (top+bottom black)"
            if left_black and right_black:
                return False, "Pillarbox bars (left+right black)"
            return True, "No black bars"
        except Exception as exc:
            # Fail closed: a corrupt screenshot is not a reason to allow through.
            self.log.error(f"Stage-1 pixel check failed (fail-closed): {exc}")
            return False, f"Stage-1 error (fail-closed): {exc}"

    # ── Stage 2: Gemini multimodal ────────────────────────────────────────────

    def _compress_for_gemini(self, screenshot_bytes: bytes, *, grayscale: bool = False) -> bytes:
        """
        FIX 4 — Vision Payload Optimization.

        Steps applied in order:
          1. Strip ancillary PNG metadata chunks (bloats encode without helping model).
          2. Resize to GEMINI_MAX_DIM on longest side.
          3. Optional grayscale conversion for binary OCR tasks (halves channel data).
          4. JPEG re-encode at quality=75; auto-drops to 60 if still >500 KB.

        A 4 MB screenshot typically reaches ~150-200 KB with near-identical
        PASS/FAIL detection quality.
        """
        try:
            img = Image.open(BytesIO(screenshot_bytes))

            # Strip ancillary PNG chunks (iCCP, tEXt, etc.)
            if img.format == "PNG" or screenshot_bytes[:4] == b"\x89PNG":
                clean = Image.new(img.mode, img.size)
                clean.putdata(list(img.getdata()))
                img = clean

            max_dim = Config.GEMINI_MAX_DIM
            if max(img.width, img.height) > max_dim:
                scale = max_dim / max(img.width, img.height)
                img = img.resize(
                    (int(img.width * scale), int(img.height * scale)),
                    Image.Resampling.LANCZOS,
                )

            if grayscale:
                img = img.convert("L")
            elif img.mode in ("RGBA", "P", "LA"):
                img = img.convert("RGB")

            buf = BytesIO()
            img.save(buf, format="JPEG", quality=75, optimize=True)
            compressed = buf.getvalue()

            # Auto-drop to quality=60 if still large after resize
            if len(compressed) > 500_000:
                buf2 = BytesIO()
                img.save(buf2, format="JPEG", quality=60, optimize=True)
                alt = buf2.getvalue()
                if len(alt) < len(compressed):
                    compressed = alt

            self.log.debug(
                f"Compressed for Gemini: {len(screenshot_bytes)//1024}KB "
                f"→ {len(compressed)//1024}KB"
                + (" (grayscale)" if grayscale else "")
            )
            return compressed
        except Exception as exc:
            self.log.debug(f"_compress_for_gemini fallback (PIL error): {exc}")
            return screenshot_bytes

    def _is_retryable(self, exc: Exception) -> bool:
        msg = str(exc).lower()
        return any(tok in msg for tok in self._RETRYABLE_MESSAGES)

    def _metric_fallback(self, views: int, likes: int, reason: str) -> Tuple[bool, str]:
        """Fallback only when AI providers are unavailable, never after an AI FAILED decision."""
        if not Config.ENABLE_GEMINI_FALLBACK:
            return False, f"AI unavailable and metric fallback disabled: {reason}"
        passed = views >= Config.FALLBACK_MIN_VIEWS and likes >= Config.FALLBACK_MIN_LIKES
        decision = "PASSED" if passed else "FAILED"
        return (
            passed,
            f"Metric fallback {decision}: views={views:,}/{Config.FALLBACK_MIN_VIEWS:,}, "
            f"likes={likes:,}/{Config.FALLBACK_MIN_LIKES:,} ({reason})",
        )

    @staticmethod
    def _is_model_decision(reason: str) -> bool:
        """True only when a fallback vision model actually returned PASSED/FAILED."""
        return reason.startswith((
            "Gemini Web Vision: PASSED",
            "Gemini Web Vision: FAILED",
            "HF LLaVA Vision: PASSED",
            "HF LLaVA Vision: FAILED",
        ))

    def check_with_gemini(self, screenshot_bytes: bytes, views: int = 0, likes: int = 0) -> Tuple[bool, str]:
        compressed = self._compress_for_gemini(screenshot_bytes, grayscale=True)

        # No API key / SDK: try browser vision when enabled, then HF LLaVA,
        # then the explicit metrics fallback. _ask_gemini_web handles the HF path
        # even when browser-based Gemini is disabled.
        if not self.gemini_enabled:
            result, reason = self._ask_gemini_web(compressed, reason="Gemini API unavailable")
            if self._is_model_decision(reason):
                return result, reason
            return self._metric_fallback(views, likes, reason)

        # Once quota is known to be exhausted, do not hit the API again this run.
        if self._api_quota_hit:
            result, reason = self._ask_gemini_web(compressed, reason="Gemini quota cached")
            if self._is_model_decision(reason):
                return result, reason
            return self._metric_fallback(views, likes, reason)

        last_exc: Optional[Exception] = None
        quota_exhausted = False
        for attempt in range(1, Config.GEMINI_RETRIES + 2):
            try:
                raw_text = self._ai._try_gemini(
                    self._GEMINI_PROMPT,
                    image_bytes=compressed,
                    max_output_tokens=32,
                )
                self.log.info("Gemini raw response (attempt %d): %r", attempt, raw_text)
                if raw_text is None:
                    return False, "Gemini blocked/empty response (FAILED)"

                upper = raw_text.upper()
                if "PASSED" in upper:
                    return True, "Gemini Vision: PASSED"
                if "FAILED" in upper:
                    return False, "Gemini Vision: FAILED"
                return False, f"Gemini ambiguous response: {raw_text!r}"

            except Exception as exc:
                last_exc = exc
                exc_str = str(exc).lower()
                quota_exhausted = any(
                    tok in exc_str for tok in ("quota", "resource_exhausted", "rate", "429")
                )
                self.log.warning(
                    "Gemini error (attempt %d): %s: %s",
                    attempt, type(exc).__name__, exc,
                )
                # Quota failures are already cached by AIProviderRouter; retrying
                # them would only waste the run.
                if quota_exhausted:
                    break
                if self._is_retryable(exc) and attempt <= Config.GEMINI_RETRIES:
                    wait = min(8, 2 ** attempt)
                    self.log.info("Retrying Gemini in %ds...", wait)
                    time.sleep(wait)
                    continue
                break

        if quota_exhausted:
            self._api_quota_hit = True

        result, reason = self._ask_gemini_web(
            compressed,
            reason="API quota hit" if quota_exhausted else "API error",
        )
        # Preserve a real fallback-model decision. Metrics are used only when
        # every configured vision provider was unavailable or unusable.
        if self._is_model_decision(reason):
            return result, reason

        api_reason = f"Gemini API error: {type(last_exc).__name__}: {last_exc}"
        return self._metric_fallback(views, likes, f"{api_reason}; fallback: {reason}")

    def _ask_gemini_web(self, compressed: bytes, reason: str = "") -> Tuple[bool, str]:
        """Ask GeminiWebBrowser, fall back to HF LLaVA if it fails."""
        tag = f" ({reason})" if reason else ""

        if self._gemini_web_browser is not None:
            try:
                response_text, snap = self._gemini_web_browser.ask(
                    self._GEMINI_PROMPT,
                    image_bytes=compressed,
                )
                if snap and self._notifier:
                    try:
                        self._notifier.send_photo(
                            snap,
                            caption=f"🌐 <b>Gemini Web Vision Check</b>{tag}",
                        )
                    except Exception as se:
                        self.log.warning(f"Could not send Gemini Web screenshot: {se}")
                if response_text:
                    upper = response_text.upper()
                    if "PASSED" in upper:
                        return True, f"Gemini Web Vision: PASSED{tag}"
                    if "FAILED" in upper:
                        return False, f"Gemini Web Vision: FAILED{tag}"
                self.log.warning("Gemini Web no usable response — trying HF LLaVA...")
            except Exception as we:
                self.log.warning(f"GeminiWebBrowser failed: {we} — trying HF LLaVA...")
        else:
            self.log.info("Gemini Web not available — trying HF LLaVA directly...")

        return self._ask_hf_llava(compressed, reason=reason)

    def _ask_hf_llava(self, compressed: bytes, reason: str = "") -> Tuple[bool, str]:
        """Call the HF Space LLaVA API. Returns (passed, reason_str)."""
        tag = f" ({reason})" if reason else ""
        try:
            from ollama_vision import is_configured, ask_vision
            if not is_configured():
                self.log.warning("HF LLaVA not configured (OLLAMA_BASE_URL not set) — fail-closed")
                return False, f"HF LLaVA: not configured (fail-closed){tag}"

            text, ok = ask_vision(
                self._GEMINI_PROMPT,
                image_bytes=compressed,
                max_tokens=16,
            )
            if ok and text:
                upper = text.upper()
                if "PASSED" in upper:
                    self.log.info(f"HF LLaVA: PASSED{tag}")
                    return True, f"HF LLaVA Vision: PASSED{tag}"
                if "FAILED" in upper:
                    self.log.info(f"HF LLaVA: FAILED{tag}")
                    return False, f"HF LLaVA Vision: FAILED{tag}"
            self.log.warning(f"HF LLaVA no usable response: {text!r} — fail-closed")
            return False, f"HF LLaVA: no usable response (fail-closed){tag}"
        except Exception as exc:
            self.log.error(f"HF LLaVA fallback failed: {exc}")
            return False, f"HF LLaVA error (fail-closed): {exc}{tag}"

    # ── Startup self-test ────────────────────────────────────────────────────

    def test_gemini(self) -> tuple:
        """Run a tiny multimodal startup probe against the configured Gemini model."""
        if not self.gemini_enabled:
            return False, "GEMINI_API_KEY is not set or google-genai is unavailable"

        try:
            img = Image.new("RGB", (64, 64), color=(128, 128, 128))
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=85)
            text = self._ai._try_gemini(
                "Reply with exactly one word: READY",
                image_bytes=buf.getvalue(),
                max_output_tokens=16,
            )
            if text:
                return True, f"Gemini OK — model={Config.GEMINI_MODEL!r}, response={text!r}"
            return False, "Gemini returned an empty response on the startup test"
        except Exception as exc:
            return False, f"Gemini test failed: {type(exc).__name__}: {exc}"

    # ── Combined evaluate ─────────────────────────────────────────────────────

    # ── Best-in-class hashtag prompt ─────────────────────────────────────────
    _HASHTAG_SYSTEM_PROMPT = (
        "You are a TikTok hashtag strategist for a GTA VI / GTA 6 content page. "
        "Generate hashtags ONLY for GTA VI-related reposts. "
        "Prioritize specific GTA VI entities and topics visible in the frame/caption "
        "(Lucia, Jason, Vice City, Leonida, Rockstar Games, trailer, gameplay, cars, locations).\n"
        "Never generate tags for unrelated games or generic movie/anime/car content.\n"
        "Include a balanced mix of:\n"
        "  * 3-5 GTA VI-specific tags (#gta6 #gtavi #grandtheftauto6 etc.)\n"
        "  * 2-4 content-specific tags based on the reel\n"
        "  * 1-2 broad gaming/reach tags (#gaming #fyp #viral)\n"
        "Do not include #instagram, #reels, #tiktok, creator handles, or source-credit tags.\n"
        "OUTPUT FORMAT: Return ONLY hashtags separated by spaces. "
        "Minimum 8, maximum 12 hashtags total."
    )

    _HASHTAG_USER_PROMPT_TMPL = (
        "Analyze this GTA VI reel and generate the best TikTok hashtags.\n\n"
        "REEL CONTEXT:\n"
        "  Views: {views}\n"
        "  Likes: {likes}\n"
        "  Caption: {caption}\n\n"
        "Only output GTA VI/GTA 6-relevant hashtags. "
        "Generate 8-12 TikTok hashtags (space-separated, each starting with #). "
        "No explanation."
    )

    def suggest_hashtags(
        self,
        screenshot_bytes: bytes,
        views: int = 0,
        likes: int = 0,
        caption: str = "",
        limit: int = 12,
    ) -> list[str]:
        """
        Ask the configured AI stack (Gemini -> Groq -> OpenRouter) for hashtags.
        Uses a strict prompt that strips watermarks, handles, and personal tags.
        Automatically falls back across all 3 providers — always returns useful tags.
        """
        compressed = self._compress_for_gemini(screenshot_bytes)
        user_prompt = self._HASHTAG_USER_PROMPT_TMPL.format(
            views=f"{views:,}",
            likes=f"{likes:,}",
            caption=caption[:300] if caption else "(no caption)",
        )
        # For Gemini, system + user are merged (no separate system role in basic API)
        gemini_prompt = self._HASHTAG_SYSTEM_PROMPT + "\n\n" + user_prompt
        text_prompt   = self._HASHTAG_SYSTEM_PROMPT + "\n\n" + user_prompt

        raw = None

        # 1) Gemini with screenshot (best: vision-aware hashtags)
        try:
            raw = self._ai._try_gemini(gemini_prompt, image_bytes=compressed, max_output_tokens=150)
            if raw:
                self.log.info("Hashtag generation: Gemini succeeded")
        except Exception as exc:
            self.log.warning(f"Hashtag Gemini failed: {exc}")

        # 2) Groq + LLaMA text fallback
        if not raw:
            try:
                raw = self._ai._try_groq(text_prompt, max_output_tokens=150)
                if raw:
                    self.log.info("Hashtag generation: Groq/LLaMA succeeded")
            except Exception as exc:
                self.log.warning(f"Hashtag Groq failed: {exc}")

        # 3) OpenRouter free-tier fallback
        if not raw:
            try:
                raw = self._ai._try_openrouter(text_prompt, max_output_tokens=150)
                if raw:
                    self.log.info("Hashtag generation: OpenRouter succeeded")
            except Exception as exc:
                self.log.warning(f"Hashtag OpenRouter failed: {exc}")

        tags = parse_hashtags(raw or "", limit=limit)
        if len(tags) >= 4:
            return tags

        # Smart content-aware fallback — always return something TikTok-useful
        self.log.warning("All AI hashtag providers failed or returned <4 tags — using smart fallback")
        fallback_base = ["#fyp", "#viral", "#edit", "#trending"]
        caption_lower = (caption or "").lower()
        if any(w in caption_lower for w in ("anime", "amv", "naruto", "demon", "manga")):
            fallback_base += ["#animeedit", "#animetiktok", "#animeaesthetic"]
        elif any(w in caption_lower for w in ("car", "drift", "bmw", "m5", "luxury", "auto")):
            fallback_base += ["#carsedit", "#automotivelife", "#carsoftiktok"]
        elif any(w in caption_lower for w in ("movie", "scene", "edit", "cinematic", "film")):
            fallback_base += ["#cinedit", "#moviescene", "#sceneedit"]
        elif any(w in caption_lower for w in ("quote", "sigma", "motivation", "relatable")):
            fallback_base += ["#motivation", "#quotestoliveby", "#sigmagrindset"]
        else:
            fallback_base += ["#aesthetic", "#cinematic", "#editaudio"]

        seen: set[str] = set()
        result = []
        for t in (tags + fallback_base):
            key = t.lower()
            if key not in seen:
                seen.add(key)
                result.append(t)
            if len(result) >= limit:
                break
        return result

    def evaluate(self, screenshot_bytes: bytes, views: int = 0, likes: int = 0) -> Tuple[bool, str]:
        # Black-bar / pixel Stage 1 removed — cinematic edits legitimately have
        # letterboxing and must NOT be rejected for it. Only Gemini decides.
        self.log.info("Vision: Gemini-only evaluation (black bar check removed)")
        ok, reason = self.check_with_gemini(screenshot_bytes, views, likes)
        if not ok:
            self.log.warning(f"Vision FAILED: {reason}")
            return False, reason
        self.log.info(f"Vision PASSED: {reason}")
        return True, "Gemini vision passed"
