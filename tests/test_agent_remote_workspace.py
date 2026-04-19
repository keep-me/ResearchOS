from packages.agent.tools.tool_registry import get_openai_tools
from packages.domain.schemas import AgentChatRequest, AgentMessage


def _tool_names(*, workspace_server_id: str | None = None) -> set[str]:
    return {
        item["function"]["name"]
        for item in get_openai_tools("build", workspace_server_id=workspace_server_id)
    }


def test_agent_chat_request_accepts_workspace_server_id():
    request = AgentChatRequest(
        messages=[AgentMessage(role="user", content="hello")],
        workspace_path="/tmp/workspace",
        workspace_server_id="ssh-dev",
    )

    assert request.workspace_server_id == "ssh-dev"


def test_agent_chat_request_accepts_structured_content_and_message_meta():
    request = AgentChatRequest(
        messages=[
            AgentMessage(
                role="user",
                content=[
                    {"type": "text", "text": "请看附件"},
                    {
                        "type": "file",
                        "url": "https://example.com/paper.pdf",
                        "filename": "paper.pdf",
                        "mime": "application/pdf",
                    },
                ],
                tools={"bash": False},
                system="只输出摘要",
                variant="high",
                format={"type": "json_schema", "name": "summary"},
            )
        ],
    )

    assert request.messages[0].content == [
        {"type": "text", "text": "请看附件"},
        {
            "type": "file",
            "url": "https://example.com/paper.pdf",
            "filename": "paper.pdf",
            "mime": "application/pdf",
        },
    ]
    assert request.messages[0].tools == {"bash": False}
    assert request.messages[0].system == "只输出摘要"
    assert request.messages[0].variant == "high"
    assert request.messages[0].format == {"type": "json_schema", "name": "summary"}


def test_remote_workspace_hides_local_only_path_tools():
    names = _tool_names(workspace_server_id="ssh-dev")

    assert "inspect_workspace" in names
    assert "read_workspace_file" in names
    assert "run_workspace_command" in names
    assert "bash" not in names
    assert "read" not in names
    assert "write" not in names
    assert "edit" not in names
    assert "websearch" in names
    assert "search_web" not in names


def test_local_workspace_keeps_path_tools_available():
    names = _tool_names(workspace_server_id="local")

    assert "bash" in names
    assert "read" in names
    assert "write" in names
    assert "inspect_workspace" not in names
    assert "run_workspace_command" not in names

