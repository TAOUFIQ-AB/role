#!/usr/bin/env python3
# ─────────────────────────────────────────────────────────────────────────────
# ai_router.py — Best-effort LLM routing for text + hashtag generation
#
# Priority:
#   1) Gemini (vision-capable; preferred when an image is available)
#   2) Groq    (OpenAI-compatible text fallback)
#   3) OpenRouter (OpenAI-compatible text fallback; can use openrouter/free)
#
# Reliability updates (2026-09):
#   • Instant quota failover — 429/RESOURCE_EXHAUSTED never retries Gemini;
#     switches to Groq/OpenRouter immediately
#   • Provider health cache — quota-failed provider is disabled for 5 minutes
#     so subsequent requests skip it entirely instead of hammering dead quota
#   • Gemini concurrency semaphore — prevents burst storms that spike RPM
#   • Image compression before Gemini Vision — reduces TPM consumption
#   • Context/prompt truncation guard — prevents token explosion
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import logging
import re
import threading
import time
from typing import Optional

import requests

from config import Config

try:
    from google import genai
    from google.genai import types as genai_types
except Exception:
    genai = None
    genai_types = None


# ── Quota / rate-limit error detection ───────────────────────────────────────

_QUOTA_SIGNALS = (
    "429",
    "quota",
    "resource_exhausted",
    "rate limit",
    "rate_limit",
    "too many requests",
    "resourceexhausted",
)

def _is_quota_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(sig in msg for sig in _QUOTA_SIGNALS)


# ── Per-provider health cache (thread-safe) ───────────────────────────────────
# FIX 3 — Exponential backoff:
#   First quota hit  → cooldown = INITIAL (default 5 min)
#   Second hit       → cooldown = INITIAL * 2 (10 min)
#   Third hit        → cooldown = INITIAL * 4 (20 min)  … up to MAX (1 hour)
#   Formula: min(MAX, INITIAL * 2^(failure_count - 1))
# This prevents stampeding when quota resets are delayed (e.g. Gemini RPM
# windows reset asynchronously — a fixed 5-minute cooldown can fire again
# the instant it expires and immediately re-hit the same limit).

class _ProviderHealth:
    def __init__(self):
        self._lock = threading.Lock()
        self._disabled_until: dict[str, float] = {}
        self._failure_count:  dict[str, int]   = {}

    def mark_quota_failed(self, provider: str) -> None:
        with self._lock:
            count = self._failure_count.get(provider, 0) + 1
            self._failure_count[provider] = count
            initial = getattr(Config, "PROVIDER_COOLDOWN_INITIAL", 300)
            maximum = getattr(Config, "PROVIDER_COOLDOWN_MAX",     3600)
            cooldown = min(maximum, initial * (2 ** (count - 1)))
            until = time.time() + cooldown
            self._disabled_until[provider] = until
            logging.getLogger("AIProviderRouter").warning(
                f"[quota] {provider} quota hit (failure #{count}) — "
                f"disabling for {cooldown}s "
                f"(until {time.strftime('%H:%M:%S', time.localtime(until))})"
            )

    def is_available(self, provider: str) -> bool:
        with self._lock:
            until = self._disabled_until.get(provider, 0)
            if time.time() >= until:
                return True
            remaining = int(until - time.time())
            logging.getLogger("AIProviderRouter").debug(
                f"[quota] {provider} still cooling down ({remaining}s remaining)"
            )
            return False

    def reset(self, provider: str) -> None:
        with self._lock:
            self._disabled_until.pop(provider, None)
            self._failure_count.pop(provider, None)  # successful call resets failure count


# Provider quotas are independent. OpenRouter is billed/rate-limited by the
# OpenRouter account even when it routes to a model from another vendor, so a
# Gemini API cooldown must not disable OpenRouter choices.

_health = _ProviderHealth()

# Semaphore: max 2 concurrent Gemini API calls to stay within RPM limits
_gemini_semaphore = threading.Semaphore(2)


# ── Image compression helper ──────────────────────────────────────────────────
# FIX 4 — Vision Payload Optimization:
#   • Resize to max_dim (was already in place)
#   • JPEG recompression with quality tuned by image size (saves 60-80% TPM)
#   • Grayscale conversion for OCR-mode tasks (half the channel data)
#   • Aggressive PNG metadata stripping before encode
#   A 4MB screenshot can reach ~180KB with near-identical OCR quality.

def _compress_image(
    image_bytes: bytes,
    max_dim: int = 720,
    *,
    grayscale: bool = False,
    jpeg_quality: int = 75,
) -> bytes:
    """
    Resize + recompress an image before sending to Gemini Vision.

    grayscale=True  converts to L-mode first, cutting channel data in half.
                    Use for OCR / watermark-detection tasks where colour is
                    irrelevant.  Do NOT use for aesthetic/style evaluations.
    jpeg_quality    default 75 — good balance of visual fidelity vs. size.
                    Drops to 60 automatically when the image is very large
                    (> 500 KB after resize) to keep payloads tiny.
    """
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(image_bytes))

        # Strip ancillary PNG chunks (iCCP, tEXt, zTXt, iTXt, cHRM, etc.)
        # that bloat the encoded size without contributing to model quality.
        if img.format == "PNG" or image_bytes[:4] == b"\x89PNG":
            clean = Image.new(img.mode, img.size)
            clean.putdata(list(img.getdata()))
            img = clean

        # Resize to max_dim on longest side
        w, h = img.size
        if max(w, h) > max_dim:
            scale = max_dim / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

        # Grayscale conversion for OCR / binary-decision prompts
        if grayscale:
            img = img.convert("L")
        elif img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")

        # Auto-tune JPEG quality: if the resized image still encodes large,
        # drop quality to 60 to stay well under Gemini's TPM budget.
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=jpeg_quality, optimize=True)
        compressed = buf.getvalue()

        if len(compressed) > 500_000 and jpeg_quality > 60:
            buf2 = io.BytesIO()
            img.save(buf2, format="JPEG", quality=60, optimize=True)
            alt = buf2.getvalue()
            if len(alt) < len(compressed):
                compressed = alt

        logging.getLogger("AIProviderRouter").debug(
            f"Image compressed: {len(image_bytes)//1024}KB → {len(compressed)//1024}KB"
            + (" (grayscale)" if grayscale else "")
        )
        return compressed
    except Exception:
        return image_bytes


class AIProviderRouter:
    def __init__(self) -> None:
        self.log = logging.getLogger("AIProviderRouter")
        self._gemini_client = None
        self._providers = [
            p for p in Config.AI_PROVIDER_ORDER
            if p in {"gemini", "groq", "openrouter"}
        ]

        if Config.GEMINI_API_KEY and genai is not None:
            try:
                self._gemini_client = genai.Client(api_key=Config.GEMINI_API_KEY)
                self.log.info("Gemini text/vision provider ready: %s", Config.GEMINI_MODEL)
            except Exception as exc:
                self.log.warning("Gemini init failed: %s", exc)
                self._gemini_client = None
        elif Config.GEMINI_API_KEY:
            self.log.warning(
                "GEMINI_API_KEY is set but google-genai is not installed; Gemini API disabled."
            )

    @property
    def gemini_ready(self) -> bool:
        return self._gemini_client is not None

    def _response_text(self, response) -> Optional[str]:
        if response is None:
            return None
        try:
            text = getattr(response, "text", None)
        except Exception:
            text = None
        if isinstance(text, str) and text.strip():
            return text.strip()
        candidates = getattr(response, "candidates", None) or []
        for cand in candidates:
            content = getattr(cand, "content", None)
            parts = getattr(content, "parts", None) or []
            for part in parts:
                ptext = getattr(part, "text", None)
                if isinstance(ptext, str) and ptext.strip():
                    return ptext.strip()
        return None

    def _chat_completion(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        prompt: str,
        system_prompt: str,
        timeout: int = 45,
        max_tokens: int = 256,
        extra_headers: Optional[dict] = None,
    ) -> Optional[str]:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        if extra_headers:
            headers.update(extra_headers)

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "max_tokens": max_tokens,
        }

        resp = requests.post(base_url, headers=headers, json=payload, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            return None
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    txt = item.get("text")
                    if isinstance(txt, str):
                        parts.append(txt)
            combined = "".join(parts).strip()
            if combined:
                return combined
        return None

    def _try_gemini(
        self,
        prompt: str,
        image_bytes: Optional[bytes] = None,
        mime_type: str = "image/jpeg",
        max_output_tokens: int = 256,
    ) -> Optional[str]:
        """Call the current Google Gen AI SDK with quota-aware failover."""
        if not self._gemini_client or genai_types is None:
            return None

        if not _health.is_available("gemini"):
            self.log.debug("Gemini skipped — quota cooldown active.")
            return None

        if image_bytes is not None:
            max_dim = getattr(Config, "GEMINI_MAX_DIM", 720)
            image_bytes = _compress_image(image_bytes, max_dim=max_dim, grayscale=True)

        prompt = prompt[:8000]
        contents = [genai_types.Part.from_text(text=prompt)]
        if image_bytes is not None:
            contents.append(genai_types.Part.from_bytes(data=image_bytes, mime_type=mime_type))

        with _gemini_semaphore:
            try:
                response = self._gemini_client.models.generate_content(
                    model=Config.GEMINI_MODEL,
                    contents=contents,
                    config=genai_types.GenerateContentConfig(
                        temperature=0.0,
                        max_output_tokens=max_output_tokens,
                    ),
                )
                _health.reset("gemini")
                return self._response_text(response)
            except Exception as exc:
                if _is_quota_error(exc):
                    _health.mark_quota_failed("gemini")
                raise

    def _try_groq(self, prompt: str, *, max_output_tokens: int = 256) -> Optional[str]:
        if not Config.GROQ_API_KEY:
            return None
        if not _health.is_available("groq"):
            self.log.debug("Groq skipped — quota cooldown active.")
            return None

        models = (
            [Config.GROQ_MODEL]
            if Config.GROQ_MODEL != "auto"
            else Config.GROQ_MODEL_CASCADE
        )
        for model in models:
            try:
                result = self._chat_completion(
                    base_url="https://api.groq.com/openai/v1/chat/completions",
                    api_key=Config.GROQ_API_KEY,
                    model=model,
                    prompt=prompt[:8000],
                    system_prompt=(
                        "You are a concise assistant that returns only the requested text. "
                        "Do not add commentary unless explicitly asked."
                    ),
                    max_tokens=max_output_tokens,
                )
                if result:
                    _health.reset("groq")
                    self.log.info("Groq model succeeded: %s", model)
                    return result
            except Exception as exc:
                if _is_quota_error(exc):
                    _health.mark_quota_failed("groq")
                    break  # stop trying other Groq models too
                self.log.warning("Groq model %s failed: %s — trying next", model, exc)
        return None

    def _try_openrouter(self, prompt: str, *, max_output_tokens: int = 256) -> Optional[str]:
        if not Config.OPENROUTER_API_KEY:
            return None
        if not _health.is_available("openrouter"):
            self.log.debug("OpenRouter skipped — quota cooldown active.")
            return None

        models = (
            [Config.OPENROUTER_MODEL]
            if Config.OPENROUTER_MODEL != "auto"
            else Config.OPENROUTER_MODEL_CASCADE
        )

        for model in models:
            try:
                result = self._chat_completion(
                    base_url="https://openrouter.ai/api/v1/chat/completions",
                    api_key=Config.OPENROUTER_API_KEY,
                    model=model,
                    prompt=prompt[:8000],
                    system_prompt=(
                        "You are a concise assistant that returns only the requested text. "
                        "Do not add commentary unless explicitly asked."
                    ),
                    max_tokens=max_output_tokens,
                    extra_headers={
                        **({"HTTP-Referer": Config.OPENROUTER_SITE_URL}
                           if Config.OPENROUTER_SITE_URL else {}),
                        "X-Title": Config.OPENROUTER_APP_NAME,
                    },
                )
                if result:
                    _health.reset("openrouter")
                    self.log.info("OpenRouter model succeeded: %s", model)
                    return result
            except Exception as exc:
                if _is_quota_error(exc):
                    _health.mark_quota_failed("openrouter")
                    break
                self.log.warning("OpenRouter model %s failed: %s — trying next", model, exc)
        return None

    def complete(
        self,
        prompt: str,
        *,
        image_bytes: Optional[bytes] = None,
        mime_type: str = "image/jpeg",
        max_output_tokens: int = 256,
    ) -> Optional[str]:
        """Best-effort completion with quota-aware provider fallback."""
        tried: list[str] = []
        has_image = image_bytes is not None

        if "gemini" in self._providers and self._gemini_client is not None:
            tried.append("gemini")
            if _health.is_available("gemini"):
                try:
                    result = self._try_gemini(
                        prompt,
                        image_bytes=image_bytes,
                        mime_type=mime_type,
                        max_output_tokens=max_output_tokens,
                    )
                    if result:
                        return result
                except Exception as exc:
                    if _is_quota_error(exc):
                        cooldown = getattr(Config, "PROVIDER_COOLDOWN_INITIAL", 300)
                        self.log.warning(
                            "Gemini quota exhausted — failing over immediately; "
                            "initial cooldown is %ds.",
                            cooldown,
                        )
                    else:
                        self.log.warning("Gemini request failed: %s", exc)
            else:
                self.log.info("Gemini skipped (quota cooldown active).")

        # Browser-based Gemini is intentionally handled by VisionEvaluator, where
        # the shared Playwright context and notifier are available. Keeping that
        # logic out of this router avoids launching a second disconnected browser.
        if has_image:
            return None

        for provider in self._providers:
            if provider in tried:
                continue
            try:
                if provider == "groq":
                    result = self._try_groq(prompt, max_output_tokens=max_output_tokens)
                elif provider == "openrouter":
                    result = self._try_openrouter(prompt, max_output_tokens=max_output_tokens)
                else:
                    continue
                if result:
                    return result
            except Exception as exc:
                self.log.warning("%s request failed: %s", provider, exc)

        self.log.error("All configured text providers exhausted — no result returned.")
        return None


_HASHTAG_RE = re.compile(r"#[A-Za-z0-9_]{2,}")


def parse_hashtags(raw_text: str, limit: int = 10) -> list[str]:
    """Extract and normalize hashtags from model output."""
    if not raw_text:
        return []

    seen: set[str] = set()
    tags: list[str] = []
    for tag in _HASHTAG_RE.findall(raw_text):
        clean = "#" + tag.lstrip("#").lower()
        if clean not in seen:
            seen.add(clean)
            tags.append(clean)
        if len(tags) >= limit:
            break
    return tags
