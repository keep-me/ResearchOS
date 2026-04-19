from __future__ import annotations

import json
import sys
import uuid


def send(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def build_prompt_text(params: dict) -> str:
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


def main() -> int:
    pending_prompt_request_id: int | None = None
    pending_session_id: str | None = None
    pending_prompt_text = ""
    permission_request_id = 91001

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        message = json.loads(line)
        request_id = message.get("id")
        method = str(message.get("method") or "")
        params = message.get("params") if isinstance(message.get("params"), dict) else {}

        if method == "initialize":
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": 1,
                        "serverInfo": {
                            "name": "mock-acp-permission",
                            "version": "0.1.0",
                        },
                    },
                }
            )
            continue

        if method == "session/new":
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "sessionId": f"session-{uuid.uuid4().hex[:8]}",
                    },
                }
            )
            continue

        if method == "session/prompt":
            pending_prompt_request_id = int(request_id)
            pending_session_id = str(params.get("sessionId") or "session-missing")
            pending_prompt_text = build_prompt_text(params)
            send(
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": pending_session_id,
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
            send(
                {
                    "jsonrpc": "2.0",
                    "id": permission_request_id,
                    "method": "session/request_permission",
                    "params": {
                        "sessionId": pending_session_id,
                        "options": [
                            {"optionId": "allow_once", "name": "Allow once", "kind": "allow_once"},
                            {"optionId": "allow_always", "name": "Always allow", "kind": "allow_always"},
                            {"optionId": "reject_once", "name": "Reject", "kind": "reject_once"},
                        ],
                        "toolCall": {
                            "toolCallId": "acp-tool-1",
                            "title": "Execute ACP shell step",
                            "kind": "bash",
                            "rawInput": {
                                "command": "pytest -q",
                                "prompt": pending_prompt_text,
                            },
                        },
                    },
                }
            )
            continue

        if pending_prompt_request_id is not None and request_id == permission_request_id and not method:
            outcome = (
                message.get("result")
                if isinstance(message.get("result"), dict)
                else {}
            )
            outcome_payload = outcome.get("outcome") if isinstance(outcome.get("outcome"), dict) else {}
            option_id = str(outcome_payload.get("optionId") or "").strip()
            selected = option_id or "cancelled"
            send(
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": pending_session_id,
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
            send(
                {
                    "jsonrpc": "2.0",
                    "id": pending_prompt_request_id,
                    "result": {
                        "stopReason": "end_turn",
                    },
                }
            )
            pending_prompt_request_id = None
            pending_session_id = None
            pending_prompt_text = ""
            continue

        send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32601,
                    "message": f"unsupported method: {method}",
                },
            }
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
