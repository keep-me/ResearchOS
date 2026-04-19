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
                            "name": "mock-acp",
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
            session_id = str(params.get("sessionId") or "session-missing")
            prompt_text = build_prompt_text(params)
            content = f"MOCK_ACP_OK :: {prompt_text}" if prompt_text else "MOCK_ACP_OK"
            send(
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": session_id,
                        "update": {
                            "sessionUpdate": "agent_message_chunk",
                            "content": {
                                "type": "text",
                                "text": content,
                            },
                        },
                    },
                }
            )
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "stopReason": "end_turn",
                    },
                }
            )
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
