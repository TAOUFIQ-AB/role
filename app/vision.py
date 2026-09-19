#!/usr/bin/env python3
# vision.py — GTA VI relevance + repost-safety filter using free AI providers.

from __future__ import annotations

import logging
from io import BytesIO
from typing import Tuple

from PIL import Image

from ai_router import AIProviderRouter, parse_hashtags
from config import Config


class VisionEvaluator:
    _VISION_PROMPT = (
        "You are a strict binary visual relevance and repost-safety filter for a GTA VI / GTA 6 page.\n\n"
        "The supplied image can be a three-frame contact sheet showing early, middle and late moments "
        "from one reel, left to right. Judge the whole reel using all visible panels.\n\n"
        "Reply PASSED only when BOTH are true:\n"
        "1) The content is clearly GTA VI / GTA 6 / Grand Theft Auto VI. Strong evidence includes "
        "official GTA VI branding, Rockstar trailer imagery, Lucia, Jason, Vice City, Leonida, "
        "recognizable GTA VI scenes or unmistakable GTA VI-specific material.\n"
        "2) It is clean enough for review/repost: no TikTok/Instagram/YouTube watermark, no creator "
        "username/handle, and no source-credit overlay identifying another uploader.\n\n"
        "Reply FAILED for GTA V, GTA Online, San Andreas, other GTA titles, unrelated games, generic "
        "gaming, memes/reactions/face-cam, ambiguous material, or visible creator/platform watermarks.\n\n"
        "Official Rockstar Games and GTA VI logos are allowed.\n"
        "OUTPUT EXACTLY ONE WORD: PASSED or FAILED."
    )

    _HASHTAG_PROMPT = (
        "Generate 8-12 TikTok hashtags for this GTA VI reel. "
        "Use specific GTA VI entities visible in the image or caption such as Lucia, Jason, Vice City, "
        "Leonida, Rockstar, trailer, gameplay, cars or locations. Include #gta6 and #gtavi. "
        "Do not include creator handles, source-credit tags, #instagram or #reels. "
        "Return hashtags only, separated by spaces.\n\n"
        "Views: {views}\nLikes: {likes}\nCaption: {caption}"
    )

    def __init__(self, _legacy_gemini_key: str = "") -> None:
        self.log = logging.getLogger("VisionEvaluator")
        self._ai = AIProviderRouter()
        providers = self._ai.configured_providers
        if providers:
            self.log.info(
                "Free vision provider(s) ready: %s",
                ", ".join(providers),
            )
        else:
            self.log.warning(
                "No free AI provider configured. Set GROQ_API_KEY "
                "or OPENROUTER_API_KEY."
            )

    @staticmethod
    def _compress(screenshot_bytes: bytes) -> bytes:
        try:
            img = Image.open(BytesIO(screenshot_bytes))
            max_dim = getattr(Config, "AI_MAX_DIM", 720)
            if max(img.size) > max_dim:
                scale = max_dim / max(img.size)
                img = img.resize(
                    (max(1, int(img.width * scale)), max(1, int(img.height * scale))),
                    Image.Resampling.LANCZOS,
                )
            if img.mode != "RGB":
                img = img.convert("RGB")
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=78, optimize=True)
            return buf.getvalue()
        except Exception:
            return screenshot_bytes

    def _metric_fallback(
        self,
        views: int,
        likes: int,
        reason: str,
    ) -> Tuple[bool, str]:
        if not Config.ENABLE_GEMINI_FALLBACK:
            return False, f"AI unavailable and metric fallback disabled: {reason}"

        # A fallback should be conservative and only act on believable metrics.
        passed = (
            views >= Config.FALLBACK_MIN_VIEWS
            and likes >= Config.FALLBACK_MIN_LIKES
            and (views == 0 or likes <= views)
        )
        decision = "PASSED" if passed else "FAILED"
        return (
            passed,
            f"Metric fallback {decision}: views={views:,}/{Config.FALLBACK_MIN_VIEWS:,}, "
            f"likes={likes:,}/{Config.FALLBACK_MIN_LIKES:,} ({reason})",
        )

    def test_ai(self) -> tuple[bool, str]:
        providers = self._ai.configured_providers
        if not providers:
            return False, "No GROQ_API_KEY or OPENROUTER_API_KEY configured"

        try:
            img = Image.new("RGB", (64, 64), color=(90, 90, 90))
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=80)
            text = self._ai.complete(
                "Reply with exactly one word: READY",
                image_bytes=buf.getvalue(),
                max_output_tokens=16,
            )
            if text:
                return True, f"{providers[0]} ready — response={text!r}"
            return False, "Configured provider returned an empty response"
        except Exception as exc:
            return False, f"AI self-test failed: {type(exc).__name__}: {exc}"

    # Compatibility alias used by older code/tests.
    def test_gemini(self) -> tuple[bool, str]:
        return self.test_ai()

    def evaluate(
        self,
        screenshot_bytes: bytes,
        views: int = 0,
        likes: int = 0,
    ) -> Tuple[bool, str]:
        compressed = self._compress(screenshot_bytes)
        raw = self._ai.complete(
            self._VISION_PROMPT,
            image_bytes=compressed,
            max_output_tokens=24,
        )

        if not raw:
            result = self._metric_fallback(
                views,
                likes,
                "all configured free vision providers unavailable",
            )
            self.log.warning("Vision fallback: %s", result[1])
            return result

        upper = raw.upper()
        self.log.info("Free vision raw response: %r", raw)
        if "PASSED" in upper and "FAILED" not in upper:
            return True, "Free AI Vision: PASSED"
        if "FAILED" in upper:
            return False, "Free AI Vision: FAILED"

        self.log.warning("Ambiguous free vision response: %r", raw)
        return False, f"Free AI Vision ambiguous: {raw!r}"

    def suggest_hashtags(
        self,
        screenshot_bytes: bytes,
        views: int = 0,
        likes: int = 0,
        caption: str = "",
        limit: int = 12,
    ) -> list[str]:
        compressed = self._compress(screenshot_bytes)
        prompt = self._HASHTAG_PROMPT.format(
            views=f"{views:,}",
            likes=f"{likes:,}",
            caption=(caption or "(no caption)")[:350],
        )
        raw = self._ai.complete(
            prompt,
            image_bytes=compressed,
            max_output_tokens=160,
        )
        tags = parse_hashtags(raw or "", limit=limit)
        if len(tags) >= 4:
            return tags

        fallback = [
            "#gta6",
            "#gtavi",
            "#grandtheftauto6",
            "#rockstargames",
            "#vicecity",
            "#gaming",
            "#fyp",
            "#viral",
        ]
        lower = (caption or "").lower()
        for term, tag in (
            ("lucia", "#lucia"),
            ("jason", "#jason"),
            ("leonida", "#leonida"),
            ("trailer", "#gta6trailer"),
            ("gameplay", "#gta6gameplay"),
        ):
            if term in lower:
                fallback.append(tag)

        seen: set[str] = set()
        result: list[str] = []
        for tag in tags + fallback:
            key = tag.lower()
            if key not in seen:
                seen.add(key)
                result.append(tag)
            if len(result) >= limit:
                break
        return result
