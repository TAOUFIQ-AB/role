#!/usr/bin/env python3
# control_panel.py — Railway review/control-panel client for the hunter.

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

import requests

from config import Config


class ControlPanelClient:
    def __init__(self) -> None:
        self.log = logging.getLogger("ControlPanelClient")
        self.base_url = Config.CONTROL_PANEL_URL
        self.token = Config.CONTROL_PANEL_TOKEN
        self._session = requests.Session()
        self._oidc_token = ""
        self._oidc_expires_at = 0.0
        self.enabled = bool(
            self.base_url
            and (
                self.token
                or (
                    os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL")
                    and os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
                )
            )
        )
        if self.enabled:
            if self.token:
                self._session.headers.update({"X-Agent-Token": self.token})
                auth_mode = "static token"
            else:
                auth_mode = "GitHub OIDC"
            self.log.info(
                "Railway control panel enabled: %s (auth=%s)",
                self.base_url,
                auth_mode,
            )
        else:
            self.log.info(
                "Railway control panel disabled "
                "(CONTROL_PANEL_URL plus token or GitHub OIDC required)."
            )

    def _auth_headers(self) -> dict[str, str]:
        if self.token:
            return {"X-Agent-Token": self.token}

        now = time.time()
        if self._oidc_token and now < self._oidc_expires_at - 60:
            return {"Authorization": f"Bearer {self._oidc_token}"}

        req_url = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL", "").strip()
        req_token = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "").strip()
        if not req_url or not req_token:
            return {}

        sep = "&" if "?" in req_url else "?"
        url = req_url + sep + "audience=reels-hunter-dashboard"
        r = requests.get(
            url,
            headers={"Authorization": f"Bearer {req_token}"},
            timeout=10,
        )
        r.raise_for_status()
        token = str((r.json() or {}).get("value") or "").strip()
        if not token:
            raise RuntimeError("GitHub OIDC endpoint returned no token")

        # GitHub OIDC tokens are short lived. Cache conservatively.
        self._oidc_token = token
        self._oidc_expires_at = now + 240
        return {"Authorization": f"Bearer {token}"}

    def heartbeat(self, **state: Any) -> bool:
        if not self.enabled:
            return False
        try:
            r = self._session.post(
                f"{self.base_url}/api/agent/heartbeat",
                json=state,
                headers=self._auth_headers(),
                timeout=min(Config.CONTROL_PANEL_TIMEOUT, 12),
            )
            r.raise_for_status()
            return True
        except Exception as exc:
            self.log.debug("Control-panel heartbeat failed: %s", exc)
            return False

    def poll_commands(self) -> list[dict]:
        if not self.enabled:
            return []
        try:
            r = self._session.get(
                f"{self.base_url}/api/agent/commands",
                headers=self._auth_headers(),
                timeout=min(Config.CONTROL_PANEL_TIMEOUT, 12),
            )
            r.raise_for_status()
            data = r.json()
            commands = data.get("commands") or []
            return commands if isinstance(commands, list) else []
        except Exception as exc:
            self.log.debug("Control-panel command poll failed: %s", exc)
            return []

    def ingest(
        self,
        *,
        reel_id: str,
        reel_url: str,
        review_status: str,
        ai_decision: str = "",
        ai_reason: str = "",
        views: int = 0,
        likes: int = 0,
        metrics_source: str = "",
        metrics_confidence: str = "",
        discovery_score: float = 0.0,
        queries: str = "",
        caption: str = "",
        preview_bytes: Optional[bytes] = None,
        video_path: Optional[Path] = None,
    ) -> bool:
        if not self.enabled:
            return False

        data = {
            "reel_id": reel_id,
            "reel_url": reel_url,
            "review_status": review_status,
            "ai_decision": ai_decision,
            "ai_reason": ai_reason,
            "views": str(int(views or 0)),
            "likes": str(int(likes or 0)),
            "metrics_source": metrics_source,
            "metrics_confidence": metrics_confidence,
            "discovery_score": str(float(discovery_score or 0.0)),
            "queries": queries,
            "caption": caption[:2000],
        }

        files: dict[str, tuple] = {}
        handles = []
        try:
            if preview_bytes:
                files["preview"] = (
                    f"{reel_id}_preview.jpg",
                    preview_bytes,
                    "image/jpeg",
                )

            if (
                video_path is not None
                and Config.CONTROL_PANEL_UPLOAD_VIDEO
                and Path(video_path).exists()
            ):
                handle = Path(video_path).open("rb")
                handles.append(handle)
                files["video"] = (
                    Path(video_path).name,
                    handle,
                    "video/mp4",
                )

            r = self._session.post(
                f"{self.base_url}/api/agent/ingest",
                data=data,
                files=files or None,
                headers=self._auth_headers(),
                timeout=max(Config.CONTROL_PANEL_TIMEOUT, 25),
            )
            r.raise_for_status()
            return True
        except Exception as exc:
            self.log.warning(
                "Could not send %s to control panel: %s",
                reel_id,
                exc,
            )
            return False
        finally:
            for handle in handles:
                try:
                    handle.close()
                except Exception:
                    pass
