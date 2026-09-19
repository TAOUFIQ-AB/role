#!/usr/bin/env python3
# ai_router.py — free multimodal provider routing
#
# Production order:
#   1) Groq Qwen vision/text
#   2) OpenRouter free router
#
# Gemini is intentionally not part of the default route anymore.

from __future__ import annotations

import base64
import io
import logging
import re
import threading
import time
from typing import Optional

import requests
from PIL import Image

from config import Config


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


class _ProviderHealth:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._disabled_until: dict[str, float] = {}
        self._failure_count: dict[str, int] = {}

    def mark_quota_failed(self, provider: str) -> None:
        with self._lock:
            count = self._failure_count.get(provider, 0) + 1
            self._failure_count[provider] = count
            initial = getattr(Config, "PROVIDER_COOLDOWN_INITIAL", 300)
            maximum = getattr(Config, "PROVIDER_COOLDOWN_MAX", 3600)
            cooldown = min(maximum, initial * (2 ** (count - 1)))
            self._disabled_until[provider] = time.time() + cooldown
            logging.getLogger("AIProviderRouter").warning(
                "[quota] %s disabled for %ss after quota/rate-limit failure",
                provider,
                cooldown,
            )

    def is_available(self, provider: str) -> bool:
        with self._lock:
            return time.time() >= self._disabled_until.get(provider, 0)

    def reset(self, provider: str) -> None:
        with self._lock:
            self._disabled_until.pop(provider, None)
            self._failure_count.pop(provider, None)


_health = _ProviderHealth()


def _compress_image(
    image_bytes: bytes,
    max_dim: int = 720,
    *,
    grayscale: bool = False,
    jpeg_quality: int = 78,
) -> bytes:
    try:
        img = Image.open(io.BytesIO(image_bytes))
        if max(img.size) > max_dim:
            scale = max_dim / max(img.size)
            img = img.resize(
                (max(1, int(img.width * scale)), max(1, int(img.height * scale))),
                Image.Resampling.LANCZOS,
            )
        if grayscale:
            img = img.convert("L")
        elif img.mode not in ("RGB", "L"):
            img = img.convert("RGB")

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=jpeg_quality, optimize=True)
        return buf.getvalue()
    except Exception:
        return image_bytes


class AIProviderRouter:
    def __init__(self) -> None:
        self.log = logging.getLogger("AIProviderRouter")
        self._providers = [
            p for p in Config.AI_PROVIDER_ORDER
            if p in {"groq", "openrouter"}
        ]

    @property
    def configured_providers(self) -> list[str]:
        providers: list[str] = []
        if "groq" in self._providers and Config.GROQ_API_KEY:
            providers.append("groq")
        if "openrouter" in self._providers and Config.OPENROUTER_API_KEY:
            providers.append("openrouter")
        return providers

    def _chat_completion(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        prompt: str,
        system_prompt: str,
        image_bytes: Optional[bytes] = None,
        mime_type: str = "image/jpeg",
        timeout: int = 55,
        max_tokens: int = 256,
        extra_headers: Optional[dict] = None,
    ) -> Optional[str]:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        if extra_headers:
            headers.update(extra_headers)

        if image_bytes is not None:
            compressed = _compress_image(
                image_bytes,
                max_dim=getattr(Config, "AI_MAX_DIM", 720),
                grayscale=False,
            )
            data_uri = (
                f"data:{mime_type};base64,"
                + base64.b64encode(compressed).decode("ascii")
            )
            user_content = [
                {"type": "text", "text": prompt[:8000]},
                {"type": "image_url", "image_url": {"url": data_uri}},
            ]
        else:
            user_content = prompt[:8000]

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.0,
            "max_completion_tokens": max_tokens,
        }

        resp = requests.post(
            base_url,
            headers=headers,
            json=payload,
            timeout=timeout,
        )
        if resp.status_code >= 400:
            body = (resp.text or "")[:1200]
            raise RuntimeError(
                f"HTTP {resp.status_code} from {model}: {body}"
            )

        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            return None

        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()

        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str) and text.strip():
                        parts.append(text.strip())
            joined = "\n".join(parts).strip()
            return joined or None
        return None

    def _try_groq(
        self,
        prompt: str,
        *,
        image_bytes: Optional[bytes] = None,
        mime_type: str = "image/jpeg",
        max_output_tokens: int = 256,
    ) -> Optional[str]:
        if not Config.GROQ_API_KEY or not _health.is_available("groq"):
            return None

        if image_bytes is not None:
            models = [Config.GROQ_VISION_MODEL]
        else:
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
                    prompt=prompt,
                    image_bytes=image_bytes,
                    mime_type=mime_type,
                    system_prompt=(
                        "Return only the requested answer. "
                        "For binary classification, output exactly PASSED or FAILED."
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
                    self.log.warning("Groq quota/rate limit: %s", exc)
                    break
                self.log.warning("Groq model %s failed: %s", model, exc)
        return None

    def _try_openrouter(
        self,
        prompt: str,
        *,
        image_bytes: Optional[bytes] = None,
        mime_type: str = "image/jpeg",
        max_output_tokens: int = 256,
    ) -> Optional[str]:
        if not Config.OPENROUTER_API_KEY or not _health.is_available("openrouter"):
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
                    prompt=prompt,
                    image_bytes=image_bytes,
                    mime_type=mime_type,
                    system_prompt=(
                        "Return only the requested answer. "
                        "For binary classification, output exactly PASSED or FAILED."
                    ),
                    max_tokens=max_output_tokens,
                    extra_headers={
                        **(
                            {"HTTP-Referer": Config.OPENROUTER_SITE_URL}
                            if Config.OPENROUTER_SITE_URL else {}
                        ),
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
                    self.log.warning("OpenRouter quota/rate limit: %s", exc)
                    break
                self.log.warning("OpenRouter model %s failed: %s", model, exc)
        return None

    def complete(
        self,
        prompt: str,
        *,
        image_bytes: Optional[bytes] = None,
        mime_type: str = "image/jpeg",
        max_output_tokens: int = 256,
    ) -> Optional[str]:
        for provider in self._providers:
            try:
                if provider == "groq":
                    result = self._try_groq(
                        prompt,
                        image_bytes=image_bytes,
                        mime_type=mime_type,
                        max_output_tokens=max_output_tokens,
                    )
                elif provider == "openrouter":
                    result = self._try_openrouter(
                        prompt,
                        image_bytes=image_bytes,
                        mime_type=mime_type,
                        max_output_tokens=max_output_tokens,
                    )
                else:
                    continue
                if result:
                    return result
            except Exception as exc:
                self.log.warning("%s provider failed: %s", provider, exc)

        self.log.error(
            "No configured free AI provider returned a usable response."
        )
        return None


_HASHTAG_RE = re.compile(r"#[A-Za-z0-9_]{2,}")


def parse_hashtags(raw_text: str, limit: int = 10) -> list[str]:
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
