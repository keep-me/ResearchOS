from types import SimpleNamespace

from packages.agent import agent_runtime_policy


def test_get_max_tool_steps_respects_reasoning_profiles(monkeypatch):
    monkeypatch.setattr(
        agent_runtime_policy,
        "get_settings",
        lambda: SimpleNamespace(agent_max_tool_steps=20),
    )

    assert agent_runtime_policy.get_max_tool_steps("default") == 20
    assert agent_runtime_policy.get_max_tool_steps("low") == 10
    assert agent_runtime_policy.get_max_tool_steps("high") == 30


def test_auto_compaction_threshold_uses_shared_settings(monkeypatch):
    monkeypatch.setattr(
        agent_runtime_policy,
        "get_settings",
        lambda: SimpleNamespace(
            agent_compaction_auto=True,
            agent_compaction_fallback_context_window=128000,
            agent_compaction_reserved_tokens=20000,
        ),
    )

    assert agent_runtime_policy.get_auto_compaction_input_tokens_threshold() == 108000


def test_auto_compaction_threshold_effectively_disables_when_off(monkeypatch):
    monkeypatch.setattr(
        agent_runtime_policy,
        "get_settings",
        lambda: SimpleNamespace(agent_compaction_auto=False),
    )

    assert agent_runtime_policy.get_auto_compaction_input_tokens_threshold() > 1_000_000_000


def test_is_tool_progress_placeholder_text_matches_preamble_but_not_result_summary():
    assert agent_runtime_policy.is_tool_progress_placeholder_text("我来先帮你查一下相关资料。")
    assert agent_runtime_policy.is_tool_progress_placeholder_text("Let me search for that first.")
    assert not agent_runtime_policy.is_tool_progress_placeholder_text("已完成以下工具调用：1. search_papers: 找到 12 篇相关论文")
    assert not agent_runtime_policy.is_tool_progress_placeholder_text("根据刚才的检索结果，我建议优先看方法部分。")


def test_should_hard_stop_after_tool_request_reserves_summary_turn():
    assert not agent_runtime_policy.should_hard_stop_after_tool_request(0, 3, requested_tool_calls=True)
    assert agent_runtime_policy.should_hard_stop_after_tool_request(2, 3, requested_tool_calls=True)
    assert not agent_runtime_policy.should_hard_stop_after_tool_request(2, 3, requested_tool_calls=False)


def test_tool_call_signature_is_stable_for_same_arguments():
    first = agent_runtime_policy.tool_call_signature(
        "bash",
        {"command": "echo hi", "env": {"B": 2, "A": 1}},
    )
    second = agent_runtime_policy.tool_call_signature(
        "bash",
        {"env": {"A": 1, "B": 2}, "command": "echo hi"},
    )

    assert first == second
    assert first.startswith('bash:{"command":"echo hi","env":{"A":1,"B":2}}')


def test_should_hard_stop_after_repeated_tool_calls_only_for_identical_back_to_back_calls():
    previous = (
        agent_runtime_policy.tool_call_signature("bash", {"command": "echo repeat"}),
    )
    same = (
        agent_runtime_policy.tool_call_signature("bash", {"command": "echo repeat"}),
    )
    different = (
        agent_runtime_policy.tool_call_signature("bash", {"command": "echo other"}),
    )

    assert not agent_runtime_policy.should_hard_stop_after_repeated_tool_calls(0, previous, same)
    assert agent_runtime_policy.should_hard_stop_after_repeated_tool_calls(1, previous, same)
    assert not agent_runtime_policy.should_hard_stop_after_repeated_tool_calls(1, previous, different)
    assert not agent_runtime_policy.should_hard_stop_after_repeated_tool_calls(1, None, same)
