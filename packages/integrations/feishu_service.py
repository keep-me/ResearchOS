from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any
from urllib import error, request
from urllib.parse import urlencode


class FeishuNotificationService:
    def __init__(
        self,
        *,
        mode: str,
        webhook_url: str | None = None,
        webhook_secret: str | None = None,
        bridge_url: str | None = None,
        timeout_seconds: int = 300,
        timeout_action: str = "approve",
    ) -> None:
        self.mode = str(mode or "off").strip().lower() or "off"
        self.webhook_url = str(webhook_url or "").strip() or None
        self.webhook_secret = str(webhook_secret or "").strip() or None
        self.bridge_url = str(bridge_url or "").strip().rstrip("/") or None
        self.timeout_seconds = max(5, min(int(timeout_seconds or 300), 3600))
        normalized_timeout_action = str(timeout_action or "approve").strip().lower() or "approve"
        self.timeout_action = (
            normalized_timeout_action
            if normalized_timeout_action in {"approve", "reject", "wait"}
            else "approve"
        )

    def send_event(
        self,
        *,
        event_type: str,
        title: str,
        body: str,
        color: str = "blue",
        options: list[str] | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self.mode == "off":
            return {"sent": False, "reason": "mode_off"}

        if self.mode == "interactive" and self.bridge_url:
            result = self._send_bridge_event(
                event_type=event_type,
                title=title,
                body=body,
                options=options or [],
                context=context or {},
            )
            if result.get("sent"):
                return result
            if not self.webhook_url:
                return result

        if not self.webhook_url:
            return {"sent": False, "reason": "webhook_missing"}
        webhook_result = self._send_webhook_event(title=title, body=body, color=color)
        if self.mode == "interactive":
            webhook_result["mode"] = "interactive"
            webhook_result["bridge_sent"] = False
        return webhook_result

    def poll_reply(self) -> dict[str, Any]:
        if not self.bridge_url:
            return {"ok": False, "reason": "bridge_missing"}
        query = urlencode({"timeout": self.timeout_seconds})
        url = f"{self.bridge_url}/poll?{query}"
        return self._request_json(url)

    def _send_webhook_event(self, *, title: str, body: str, color: str) -> dict[str, Any]:
        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": title[:120]},
                    "template": color,
                },
                "elements": [
                    {
                        "tag": "markdown",
                        "content": body[:12000],
                    }
                ],
            },
        }
        return self._post_json(
            self.webhook_url,
            payload,
            signed=bool(self.webhook_secret),
        )

    def _send_bridge_event(
        self,
        *,
        event_type: str,
        title: str,
        body: str,
        options: list[str],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.bridge_url:
            return {"sent": False, "reason": "bridge_missing"}
        payload = {
            "type": event_type,
            "title": title[:120],
            "body": body[:12000],
            "options": options[:8],
            "timeout_seconds": self.timeout_seconds,
            "context": dict(context or {}),
        }
        result = self._post_json(f"{self.bridge_url}/send", payload, signed=False)
        result["mode"] = "interactive"
        result["bridge_sent"] = bool(result.get("sent"))
        return result

    def _post_json(
        self, url: str | None, payload: dict[str, Any], *, signed: bool
    ) -> dict[str, Any]:
        if not url:
            return {"sent": False, "reason": "url_missing"}
        headers = {"Content-Type": "application/json; charset=utf-8"}
        body_payload = dict(payload)
        if signed and self.webhook_secret:
            timestamp = str(int(time.time()))
            body_payload["timestamp"] = timestamp
            body_payload["sign"] = _build_sign(self.webhook_secret, timestamp)
        data = json.dumps(body_payload, ensure_ascii=False).encode("utf-8")
        req = request.Request(url, data=data, headers=headers, method="POST")
        return self._execute_request(req)

    def _request_json(self, url: str) -> dict[str, Any]:
        req = request.Request(url, headers={"Accept": "application/json"}, method="GET")
        return self._execute_request(req, expect_reply=True)

    def _execute_request(
        self, req: request.Request, *, expect_reply: bool = False
    ) -> dict[str, Any]:
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                parsed = json.loads(raw) if raw.strip().startswith("{") else {"raw": raw}
                if isinstance(parsed, dict):
                    if expect_reply:
                        return {
                            "ok": True,
                            "timeout": bool(parsed.get("timeout")),
                            "reply": parsed.get("reply"),
                            "response": parsed,
                        }
                    status = parsed.get("StatusCode")
                    code = parsed.get("code")
                    if status not in (None, 0, "0") or code not in (None, 0, "0"):
                        return {
                            "sent": False,
                            "reason": f"feishu_error:{status or code}",
                            "response": parsed,
                        }
                return {"sent": True, "response": parsed if isinstance(parsed, dict) else raw}
        except error.HTTPError as exc:
            detail = (
                exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else str(exc)
            )
            key = "ok" if expect_reply else "sent"
            return {key: False, "reason": f"http_{exc.code}", "detail": detail[:1000]}
        except Exception as exc:  # pragma: no cover - network/runtime best effort
            key = "ok" if expect_reply else "sent"
            return {key: False, "reason": str(exc)[:300]}


def _build_sign(secret: str, timestamp: str) -> str:
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(string_to_sign.encode("utf-8"), b"", digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")
