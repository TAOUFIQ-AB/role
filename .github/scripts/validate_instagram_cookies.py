#!/usr/bin/env python3
"""Validate the Instagram cookie secret before the expensive browser run.

Supports the same JSON-array and semicolon formats accepted by BrowserManager.
This is intentionally advisory for stale/missing individual cookie fields: the
only hard failure is an empty secret, which the workflow validates separately.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request


def _telegram(message: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return
    payload = json.dumps({"chat_id": chat_id, "text": message, "parse_mode": "HTML"}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as exc:
        print(f"Telegram validation notification failed: {exc}")


def _parse(raw: str) -> list[dict]:
    raw = raw.strip()
    if not raw:
        return []
    if raw.startswith("["):
        try:
            value = json.loads(raw)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        except json.JSONDecodeError as exc:
            print(f"Cookie JSON parse warning: {exc}")
            return []

    result = []
    for part in raw.split(";"):
        part = part.strip()
        if "=" not in part:
            continue
        name, _, value = part.partition("=")
        if name.strip():
            result.append({"name": name.strip(), "value": value.strip()})
    return result


def main() -> int:
    raw = os.environ.get("INSTAGRAM_SESSION_COOKIES", "").strip()
    if not raw:
        print("ERROR: INSTAGRAM_SESSION_COOKIES is empty.")
        return 2

    cookies = _parse(raw)
    if not cookies:
        print("WARNING: cookie secret is non-empty but no cookies could be parsed.")
        _telegram("⚠️ <b>Instagram cookie validation failed</b>: no cookies could be parsed.")
        return 0

    by_name = {str(c.get("name", "")): c for c in cookies}
    critical = {"sessionid", "csrftoken", "ds_user_id"}
    missing = sorted(critical - set(by_name))
    if missing:
        print("WARNING: missing recommended Instagram cookies:", ", ".join(missing))
        _telegram(
            "⚠️ <b>Instagram cookies may be incomplete</b>\nMissing: "
            + ", ".join(f"<code>{name}</code>" for name in missing)
        )

    session = by_name.get("sessionid")
    if session:
        raw_expiry = session.get("expirationDate", session.get("expires"))
        if raw_expiry not in (None, "", -1, "-1"):
            try:
                expiry = int(float(raw_expiry))
            except (TypeError, ValueError):
                expiry = 0
            if expiry > 0:
                seconds_left = expiry - int(time.time())
                days_left = seconds_left // 86400
                if seconds_left <= 0:
                    print("WARNING: sessionid appears expired.")
                    _telegram("🔴 <b>Instagram sessionid appears expired.</b> Export fresh cookies.")
                elif days_left < 7:
                    print(f"WARNING: sessionid expires in about {days_left} day(s).")
                    _telegram(f"⚠️ Instagram sessionid expires in about <b>{days_left} day(s)</b>.")
                else:
                    print(f"sessionid expiry check: about {days_left} day(s) remaining.")

    print(f"Instagram cookie validation complete: {len(cookies)} cookie(s) parsed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
