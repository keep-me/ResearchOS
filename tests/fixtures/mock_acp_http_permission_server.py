from __future__ import annotations

import contextlib
import json
import threading
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Iterator


def _build_prompt_text(params: dict[str, Any]) -> str:
    prompt_blocks = params.get("prompt") or []
    parts: list[str] = []
    for item in prompt_blocks:
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "") != "text":
            continue
        text = str(item.get("text") or "").strip()
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


@dataclass
class _PendingPermission:
    prompt_request_id: int
    session_id: str
    prompt_text: str
    response_event: threading.Event = field(default_factory=threading.Event)
    option_id: str | None = None


class _MockHttpPermissionState:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._next_permission_request_id = 91001
        self._pending_by_permission_id: dict[int, _PendingPermission] = {}

    def create_permission(self, *, prompt_request_id: int, session_id: str, prompt_text: str) -> tuple[int, _PendingPermission]:
        with self._lock:
            permission_request_id = self._next_permission_request_id
            self._next_permission_request_id += 1
            pending = _PendingPermission(
                prompt_request_id=prompt_request_id,
                session_id=session_id,
                prompt_text=prompt_text,
            )
            self._pending_by_permission_id[permission_request_id] = pending
        return permission_request_id, pending

    def resolve_permission(self, permission_request_id: int, option_id: str | None) -> bool:
        with self._lock:
            pending = self._pending_by_permission_id.get(permission_request_id)
        if pending is None:
            return False
        pending.option_id = option_id
        pending.response_event.set()
        return True

    def clear_permission(self, permission_request_id: int) -> None:
        with self._lock:
            self._pending_by_permission_id.pop(permission_request_id, None)


class _MockHttpPermissionHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    @property
    def state(self) -> _MockHttpPermissionState:
        return self.server.state  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        del format, args

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or "0")
        body = self.rfile.read(length) if length > 0 else b"{}"
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            self._send_json_response(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "invalid json"},
                },
                status=400,
            )
            return

        request_id = payload.get("id")
        method = str(payload.get("method") or "")
        params = payload.get("params") if isinstance(payload.get("params"), dict) else {}

        if method == "initialize":
            self._send_json_response(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": 1,
                        "serverInfo": {
                            "name": "mock-http-acp-permission",
                            "version": "0.1.0",
                        },
                    },
                }
            )
            return

        if method == "session/new":
            self._send_json_response(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "sessionId": f"session-{uuid.uuid4().hex[:8]}",
                    },
                }
            )
            return

        if method == "session/prompt":
            self._handle_prompt_stream(int(request_id or 0), params)
            return

        if not method and request_id is not None:
            result_payload = payload.get("result") if isinstance(payload.get("result"), dict) else {}
            outcome = result_payload.get("outcome") if isinstance(result_payload.get("outcome"), dict) else {}
            option_id = str(outcome.get("optionId") or "").strip() or None
            resolved = self.state.resolve_permission(int(request_id or 0), option_id)
            if not resolved:
                self._send_json_response(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {
                            "code": -32004,
                            "message": "permission request not found",
                        },
                    }
                )
                return
            self._send_json_response(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"ack": True},
                }
            )
            return

        self._send_json_response(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32601,
                    "message": f"unsupported method: {method}",
                },
            }
        )

    def _handle_prompt_stream(self, request_id: int, params: dict[str, Any]) -> None:
        session_id = str(params.get("sessionId") or "session-missing")
        prompt_text = _build_prompt_text(params)
        permission_request_id, pending = self.state.create_permission(
            prompt_request_id=request_id,
            session_id=session_id,
            prompt_text=prompt_text,
        )

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        try:
            self._write_chunk(
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": session_id,
                        "update": {
                            "sessionUpdate": "agent_message_chunk",
                            "content": {
                                "type": "text",
                                "text": "Permission required. ",
                            },
                        },
                    },
                }
            )
            self._write_chunk(
                {
                    "jsonrpc": "2.0",
                    "id": permission_request_id,
                    "method": "session/request_permission",
                    "params": {
                        "sessionId": session_id,
                        "options": [
                            {"optionId": "allow_once", "name": "Allow once", "kind": "allow_once"},
                            {"optionId": "allow_always", "name": "Always allow", "kind": "allow_always"},
                            {"optionId": "reject_once", "name": "Reject", "kind": "reject_once"},
                        ],
                        "toolCall": {
                            "toolCallId": "acp-http-tool-1",
                            "title": "Execute ACP HTTP shell step",
                            "kind": "bash",
                            "rawInput": {
                                "command": "pytest -q",
                                "prompt": prompt_text,
                            },
                        },
                    },
                }
            )
            if not pending.response_event.wait(timeout=15):
                self._write_chunk(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {
                            "code": -32002,
                            "message": "permission response timeout",
                        },
                    }
                )
                return

            selected = pending.option_id or "cancelled"
            self._write_chunk(
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": session_id,
                        "update": {
                            "sessionUpdate": "agent_message_chunk",
                            "content": {
                                "type": "text",
                                "text": f"Permission outcome: {selected}.",
                            },
                        },
                    },
                }
            )
            self._write_chunk(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "stopReason": "end_turn",
                    },
                }
            )
        finally:
            self.state.clear_permission(permission_request_id)
            with contextlib.suppress(Exception):
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()

    def _write_chunk(self, payload: dict[str, Any]) -> None:
        data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        self.wfile.write(f"{len(data):X}\r\n".encode("ascii"))
        self.wfile.write(data)
        self.wfile.write(b"\r\n")
        self.wfile.flush()

    def _send_json_response(self, payload: dict[str, Any], *, status: int = 200) -> None:
        data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        self.wfile.flush()


@contextlib.contextmanager
def serve_mock_acp_http_permission_server() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _MockHttpPermissionHandler)
    server.state = _MockHttpPermissionState()  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/rpc"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
