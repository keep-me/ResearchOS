"""OpenCode-like permission rules and pending approval handling for ResearchOS."""

from __future__ import annotations

import copy
import fnmatch
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from packages.agent.session.session_plan import (
    check_plan_mode_tool_access,
    plan_exit_confirmation_text,
    targets_plan_file_only,
)
from packages.agent.session.session_question import normalize_questions_payload
from packages.agent.tools.apply_patch_runtime import patch_paths
from packages.agent.tools.tool_registry import manages_tool as registry_manages_tool
from packages.agent.tools.tool_registry import tool_permission as registry_tool_permission
from packages.agent.tools.tool_registry import tool_spec as registry_tool_spec
from packages.agent.workspace.workspace_executor import (
    get_assistant_exec_policy,
    local_shell_command_to_string,
)
from packages.storage.db import session_scope
from packages.storage.repositories import (
    AgentPendingActionRepository,
    AgentPermissionRuleSetRepository,
    AgentProjectRepository,
)

PermissionAction = str
PermissionReply = str
PermissionRule = dict[str, str]
PermissionRuleset = list[PermissionRule]
_DEFAULT_GET_ASSISTANT_EXEC_POLICY = get_assistant_exec_policy


@dataclass
class PendingPermissionRequest:
    id: str
    session_id: str
    project_id: str
    permission: str
    patterns: list[str]
    metadata: dict[str, Any]
    always: list[str]
    tool: dict[str, Any] | None = None


@dataclass
class PermissionDecision:
    status: str
    permission: str
    patterns: list[str]
    always: list[str]
    request: PendingPermissionRequest | None = None
    reason: str | None = None


def _request_to_payload(request: PendingPermissionRequest) -> dict[str, Any]:
    return asdict(request)


def _request_from_payload(payload: dict[str, Any] | None) -> PendingPermissionRequest | None:
    if not isinstance(payload, dict):
        return None
    request_id = str(payload.get("id") or "").strip()
    session_id = str(payload.get("session_id") or "").strip()
    project_id = str(payload.get("project_id") or "").strip()
    permission = str(payload.get("permission") or "").strip()
    if not request_id or not session_id or not project_id or not permission:
        return None
    return PendingPermissionRequest(
        id=request_id,
        session_id=session_id,
        project_id=project_id,
        permission=permission,
        patterns=[str(item) for item in (payload.get("patterns") or []) if str(item).strip()],
        metadata=dict(payload.get("metadata") or {}),
        always=[str(item) for item in (payload.get("always") or []) if str(item).strip()],
        tool=dict(payload.get("tool") or {}) or None,
    )


def _persist_pending_request(
    request: PendingPermissionRequest, continuation_json: dict[str, Any] | None = None
) -> None:
    with session_scope() as session:
        AgentPendingActionRepository(session).upsert(
            action_id=request.id,
            session_id=request.session_id,
            project_id=request.project_id,
            action_type="permission",
            permission_json=_request_to_payload(request),
            continuation_json=continuation_json,
        )


def _delete_persisted_pending(request_id: str) -> bool:
    with session_scope() as session:
        return AgentPendingActionRepository(session).delete(request_id)


def persist_pending_action_state(
    *,
    action_id: str,
    session_id: str,
    project_id: str,
    action_type: str,
    permission_request: dict[str, Any] | None = None,
    options_payload: dict[str, Any] | None = None,
    continuation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    continuation_json: dict[str, Any] | None = None
    if isinstance(options_payload, dict) and options_payload:
        continuation_json = continuation_json or {}
        continuation_json["options"] = copy.deepcopy(options_payload)
    if isinstance(continuation, dict) and continuation:
        continuation_json = continuation_json or {}
        continuation_json["continuation"] = copy.deepcopy(continuation)

    with session_scope() as session:
        AgentPendingActionRepository(session).upsert(
            action_id=action_id,
            session_id=session_id,
            project_id=project_id,
            action_type=action_type,
            permission_json=copy.deepcopy(permission_request)
            if isinstance(permission_request, dict)
            else None,
            continuation_json=continuation_json,
        )
    return load_pending_action_state(action_id) or {
        "id": action_id,
        "session_id": session_id,
        "project_id": project_id,
        "action_type": action_type,
        "permission_request": copy.deepcopy(permission_request)
        if isinstance(permission_request, dict)
        else None,
        "options": copy.deepcopy(options_payload) if isinstance(options_payload, dict) else None,
        "continuation": copy.deepcopy(continuation) if isinstance(continuation, dict) else None,
    }


def load_pending_action_state(action_id: str) -> dict[str, Any] | None:
    with session_scope() as session:
        row = AgentPendingActionRepository(session).get(action_id)
        if row is None:
            return None
        continuation_json = (
            dict(row.continuation_json or {}) if isinstance(row.continuation_json, dict) else {}
        )
        permission_json = (
            dict(row.permission_json or {}) if isinstance(row.permission_json, dict) else None
        )
        return {
            "id": str(row.id),
            "session_id": str(row.session_id),
            "project_id": str(row.project_id),
            "action_type": str(row.action_type),
            "permission_request": permission_json,
            "options": (
                dict(continuation_json.get("options") or {})
                if isinstance(continuation_json.get("options"), dict)
                else None
            ),
            "continuation": (
                dict(continuation_json.get("continuation") or {})
                if isinstance(continuation_json.get("continuation"), dict)
                else None
            ),
        }


def delete_pending_action_state(action_id: str) -> bool:
    return _delete_persisted_pending(action_id)


def pop_pending_action_state(action_id: str) -> dict[str, Any] | None:
    payload = load_pending_action_state(action_id)
    _delete_persisted_pending(action_id)
    return payload


def expand(pattern: str) -> str:
    value = str(pattern or "").strip()
    if value.startswith("~/"):
        return str(Path.home()) + value[1:]
    if value == "~":
        return str(Path.home())
    if value.startswith("$HOME/"):
        return str(Path.home()) + value[5:]
    if value.startswith("$HOME"):
        return str(Path.home()) + value[5:]
    return value


def from_config(permission: dict[str, Any] | None) -> PermissionRuleset:
    ruleset: PermissionRuleset = []
    for key, value in (permission or {}).items():
        if isinstance(value, str):
            ruleset.append(
                {
                    "permission": str(key),
                    "pattern": "*",
                    "action": value,
                }
            )
            continue
        if isinstance(value, dict):
            for pattern, action in value.items():
                ruleset.append(
                    {
                        "permission": str(key),
                        "pattern": expand(str(pattern)),
                        "action": str(action),
                    }
                )
    return ruleset


def merge(*rulesets: PermissionRuleset) -> PermissionRuleset:
    merged: PermissionRuleset = []
    for ruleset in rulesets:
        merged.extend(list(ruleset or []))
    return merged


def evaluate(permission: str, pattern: str, *rulesets: PermissionRuleset) -> PermissionRule:
    merged = merge(*rulesets)
    normalized_permission = str(permission or "").strip() or "*"
    normalized_pattern = expand(str(pattern or "*").strip() or "*")
    for rule in reversed(merged):
        if fnmatch.fnmatchcase(
            normalized_permission, str(rule.get("permission") or "*")
        ) and fnmatch.fnmatchcase(
            normalized_pattern,
            expand(str(rule.get("pattern") or "*")),
        ):
            return {
                "permission": str(rule.get("permission") or normalized_permission),
                "pattern": expand(str(rule.get("pattern") or "*")),
                "action": str(rule.get("action") or "ask"),
            }
    return {
        "permission": normalized_permission,
        "pattern": "*",
        "action": "ask",
    }


def tool_permission(tool_name: str) -> str:
    return registry_tool_permission(tool_name)


def manages_tool(tool_name: str) -> bool:
    return registry_manages_tool(tool_name)


def _normalized_command_patterns(command: str) -> list[str]:
    cleaned = " ".join(str(command or "").strip().split())
    if not cleaned:
        return ["*"]
    return list(dict.fromkeys([cleaned, f"{cleaned} *"]))


def _normalize_path(value: str | None, *, base: str | None = None) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute() and base:
        path = Path(base).expanduser() / path
    try:
        return str(path.resolve())
    except OSError:
        return str(path)


def _path_patterns_for_call(
    call_name: str, arguments: dict[str, Any], session: dict[str, Any]
) -> list[str]:
    base = str(session.get("workspace_path") or session.get("directory") or "").strip() or None
    patterns: list[str] = []
    if call_name in {"inspect_workspace"}:
        value = _normalize_path(arguments.get("workspace_path"), base=base)
        if value:
            patterns.append(value)
    elif call_name in {"read_workspace_file", "write_workspace_file", "replace_workspace_text"}:
        workspace = _normalize_path(arguments.get("workspace_path"), base=base)
        relative = str(arguments.get("relative_path") or "").strip()
        if workspace and relative:
            value = _normalize_path(relative, base=workspace)
            if value:
                patterns.append(value)
        elif workspace:
            patterns.append(workspace)
    elif call_name in {"list", "ls", "glob", "grep"}:
        path_value = _normalize_path(arguments.get("path"), base=base) or base
        if path_value:
            patterns.append(path_value)
    elif call_name in {"read", "write", "edit"}:
        path_value = _normalize_path(arguments.get("file_path"), base=base)
        if path_value:
            patterns.append(path_value)
    elif call_name == "apply_patch":
        try:
            for item in patch_paths(str(arguments.get("patchText") or "")):
                path_value = _normalize_path(item, base=base)
                if path_value:
                    patterns.append(path_value)
        except Exception:
            pass
    elif call_name == "multiedit":
        outer = _normalize_path(arguments.get("file_path"), base=base)
        if outer:
            patterns.append(outer)
        for item in arguments.get("edits") or []:
            if not isinstance(item, dict):
                continue
            path_value = _normalize_path(item.get("file_path"), base=base or outer)
            if path_value:
                patterns.append(path_value)
    return list(dict.fromkeys(patterns))


def _project_boundary_paths(session: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("directory", "workspace_path"):
        item = _normalize_path(session.get(key))
        if item:
            values.append(item)

    project_id = str(session.get("projectID") or "").strip()
    if project_id:
        with session_scope() as db_session:
            project = AgentProjectRepository(db_session).get_by_id(project_id)
            if project is not None:
                worktree = _normalize_path(project.worktree)
                if worktree:
                    values.append(worktree)
                for sandbox in project.sandboxes_json or []:
                    item = _normalize_path(str(sandbox or ""))
                    if item:
                        values.append(item)

    return list(dict.fromkeys(values))


def _external_patterns(patterns: list[str], session: dict[str, Any]) -> list[str]:
    boundaries = [Path(item) for item in _project_boundary_paths(session)]
    out: list[str] = []
    for value in patterns:
        try:
            path = Path(value)
        except Exception:
            continue
        inside = False
        for boundary in boundaries:
            try:
                path.relative_to(boundary)
                inside = True
                break
            except ValueError:
                pass
        if not inside:
            out.append(value)
    return list(dict.fromkeys(out))


def _base_policy_ruleset(policy: dict[str, Any] | None = None) -> PermissionRuleset:
    if policy is not None:
        current = policy
    elif get_assistant_exec_policy is _DEFAULT_GET_ASSISTANT_EXEC_POLICY:
        # Keep the native prompt runtime on its historical permissive fallback
        # unless a test or caller explicitly injects a policy override.
        current = {}
    else:
        current = get_assistant_exec_policy() or {}
    workspace_access = str(current.get("workspace_access") or "read_write")
    command_execution = str(current.get("command_execution") or "full")
    approval_mode = str(current.get("approval_mode") or "off")
    allowed_prefixes = list(current.get("allowed_command_prefixes") or [])

    if workspace_access == "read_write" and command_execution == "full" and approval_mode == "off":
        return [{"permission": "*", "pattern": "*", "action": "allow"}]

    read_action = "allow" if workspace_access != "none" else "deny"
    edit_action = "deny"
    if workspace_access == "read_write":
        edit_action = "allow" if approval_mode == "off" else "ask"

    rules: PermissionRuleset = [
        {"permission": "read", "pattern": "*", "action": read_action},
        {"permission": "list", "pattern": "*", "action": read_action},
        {"permission": "grep", "pattern": "*", "action": read_action},
        {"permission": "glob", "pattern": "*", "action": read_action},
        {"permission": "codesearch", "pattern": "*", "action": read_action},
        {"permission": "edit", "pattern": "*", "action": edit_action},
        {"permission": "task", "pattern": "*", "action": "allow"},
        {"permission": "skill", "pattern": "*", "action": "allow"},
        {"permission": "webfetch", "pattern": "*", "action": "allow"},
        {"permission": "websearch", "pattern": "*", "action": "allow"},
        {"permission": "todoread", "pattern": "*", "action": "allow"},
        {
            "permission": "todowrite",
            "pattern": "*",
            "action": (
                "allow"
                if approval_mode == "off" and workspace_access == "read_write"
                else "ask"
                if workspace_access == "read_write"
                else "deny"
            ),
        },
        {
            "permission": "external_directory",
            "pattern": "*",
            "action": (
                "allow"
                if workspace_access == "read_write" and approval_mode == "off"
                else "ask"
                if workspace_access == "read_write"
                else "deny"
            ),
        },
    ]

    if command_execution == "deny":
        rules.append({"permission": "bash", "pattern": "*", "action": "deny"})
    elif command_execution == "full":
        rules.append(
            {
                "permission": "bash",
                "pattern": "*",
                "action": "allow" if approval_mode == "off" else "ask",
            }
        )
    else:
        rules.append({"permission": "bash", "pattern": "*", "action": "deny"})
        for prefix in allowed_prefixes:
            cleaned = " ".join(str(prefix or "").strip().split())
            if not cleaned:
                continue
            action = "allow" if approval_mode == "off" else "ask"
            rules.append({"permission": "bash", "pattern": cleaned, "action": action})
            rules.append({"permission": "bash", "pattern": f"{cleaned} *", "action": action})

    return rules


def get_project_rules(project_id: str) -> PermissionRuleset:
    with session_scope() as session:
        return AgentPermissionRuleSetRepository(session).get_ruleset(project_id)


def store_project_rules(project_id: str, ruleset: PermissionRuleset) -> PermissionRuleset:
    with session_scope() as session:
        row = AgentPermissionRuleSetRepository(session).replace(project_id, ruleset)
        return list(row.data_json or [])


def append_project_rules(project_id: str, rules: PermissionRuleset) -> PermissionRuleset:
    with session_scope() as session:
        row = AgentPermissionRuleSetRepository(session).append(project_id, rules)
        return list(row.data_json or [])


def effective_ruleset(
    session: dict[str, Any], policy: dict[str, Any] | None = None
) -> PermissionRuleset:
    base = _base_policy_ruleset(policy)
    session_rules = list(session.get("permission") or [])
    project_rules = get_project_rules(str(session.get("projectID") or "global"))
    return merge(base, session_rules, project_rules)


def disabled(tools: list[str], ruleset: PermissionRuleset) -> set[str]:
    result: set[str] = set()
    for tool in tools:
        if not manages_tool(tool):
            continue
        permission = tool_permission(tool)
        matched: PermissionRule | None = None
        for rule in ruleset:
            if fnmatch.fnmatchcase(permission, str(rule.get("permission") or "*")):
                matched = {
                    "permission": str(rule.get("permission") or permission),
                    "pattern": str(rule.get("pattern") or "*"),
                    "action": str(rule.get("action") or "ask"),
                }
        if matched and matched["pattern"] == "*" and matched["action"] == "deny":
            result.add(tool)
    return result


def list_pending(session_id: str | None = None) -> list[dict[str, Any]]:
    with session_scope() as session:
        repo = AgentPendingActionRepository(session)
        rows = (
            repo.list_by_session(session_id, action_type="permission")
            if session_id is not None
            else repo.list_all(action_type="permission")
        )
        payloads = [dict(row.permission_json or {}) for row in rows]
    items = [
        item
        for item in (_request_from_payload(payload) for payload in payloads)
        if item is not None
    ]
    return [asdict(item) for item in items]


def get_pending(request_id: str) -> PendingPermissionRequest | None:
    with session_scope() as session:
        row = AgentPendingActionRepository(session).get(request_id)
        payload = (
            dict(row.permission_json or {})
            if row is not None and isinstance(row.permission_json, dict)
            else None
        )
    return _request_from_payload(payload)


def _store_pending(request: PendingPermissionRequest) -> PendingPermissionRequest:
    _persist_pending_request(request)
    return request


def create_request(
    *,
    request_id: str | None = None,
    session_id: str,
    project_id: str,
    permission: str,
    patterns: list[str],
    metadata: dict[str, Any] | None = None,
    always: list[str] | None = None,
    tool: dict[str, Any] | None = None,
) -> PendingPermissionRequest:
    return _store_pending(
        PendingPermissionRequest(
            id=str(request_id or f"permission_{uuid4().hex}"),
            session_id=session_id,
            project_id=project_id,
            permission=permission,
            patterns=list(dict.fromkeys(patterns or ["*"])),
            metadata=dict(metadata or {}),
            always=list(dict.fromkeys(always or patterns or ["*"])),
            tool=tool,
        )
    )


def reply(
    request_id: str, response: PermissionReply, message: str | None = None
) -> PendingPermissionRequest | None:
    existing = get_pending(request_id)
    if existing is None:
        return None

    if response == "reject":
        with session_scope() as session:
            repo = AgentPendingActionRepository(session)
            rows = repo.list_by_session(existing.session_id, action_type="permission")
            repo.delete_by_ids([row.id for row in rows])
        return existing

    if response == "always":
        rules = [
            {
                "permission": existing.permission,
                "pattern": pattern,
                "action": "allow",
            }
            for pattern in existing.always
        ]
        append_project_rules(existing.project_id, rules)
        combined = get_project_rules(existing.project_id)
        removable_ids: list[str] = []
        with session_scope() as session:
            repo = AgentPendingActionRepository(session)
            for row in repo.list_by_session(existing.session_id, action_type="permission"):
                pending = _request_from_payload(row.permission_json)
                if pending is None:
                    removable_ids.append(row.id)
                    continue
                if all(
                    evaluate(pending.permission, pattern, combined).get("action") == "allow"
                    for pattern in pending.patterns
                ):
                    removable_ids.append(row.id)
            repo.delete_by_ids(removable_ids)
        return existing

    _delete_persisted_pending(request_id)
    return existing


def authorize_tool_call(
    call: Any,
    session: dict[str, Any],
    policy: dict[str, Any] | None = None,
    *,
    create_pending_request: bool = True,
) -> PermissionDecision:
    permission = tool_permission(getattr(call, "name", "") or "")
    call_name = str(getattr(call, "name", "") or "")
    arguments = dict(getattr(call, "arguments", {}) or {})
    session_id = str(session.get("id") or session.get("session_id") or "")
    project_id = str(session.get("projectID") or "global")
    violation = check_plan_mode_tool_access(
        call_name,
        arguments,
        session,
        allow_in_read_only=registry_tool_spec(call_name).allow_in_read_only,
    )
    if violation:
        return PermissionDecision(
            status="deny",
            permission=permission,
            patterns=["*"],
            always=["*"],
            reason=violation,
        )
    if call_name == "question":
        questions = normalize_questions_payload(arguments.get("questions"))
        if not questions:
            return PermissionDecision(
                status="deny",
                permission="question",
                patterns=["question"],
                always=["question"],
                reason="question 工具至少需要一个有效问题",
            )
        title = (
            f"智能体需要你回答 {len(questions)} 个问题"
            if len(questions) > 1
            else "智能体需要你补充一个问题答案"
        )
        description = (
            str(questions[0].get("question") or "")
            if len(questions) == 1
            else "请先回答这些问题，之后智能体会继续按你的回答推进。"
        )
        request = (
            create_request(
                session_id=session_id,
                project_id=project_id,
                permission="question",
                patterns=["question"],
                metadata={
                    "tool": call_name,
                    "arguments": {
                        **arguments,
                        "questions": questions,
                    },
                    "questions": questions,
                    "title": title,
                    "description": description,
                },
                always=["question"],
                tool={"callID": getattr(call, "id", ""), "messageID": ""},
            )
            if create_pending_request
            else None
        )
        return PermissionDecision(
            status="ask",
            permission="question",
            patterns=["question"],
            always=["question"],
            request=request,
        )
    if call_name == "plan_exit":
        metadata = {
            "tool": call_name,
            "arguments": arguments,
            "title": plan_exit_confirmation_text(session),
            "description": plan_exit_confirmation_text(session),
        }
        request = (
            create_request(
                session_id=session_id,
                project_id=project_id,
                permission="plan",
                patterns=["*"],
                metadata=metadata,
                always=["*"],
                tool={"callID": getattr(call, "id", ""), "messageID": ""},
            )
            if create_pending_request
            else None
        )
        return PermissionDecision(
            status="ask",
            permission="plan",
            patterns=["*"],
            always=["*"],
            request=request,
        )

    if permission == "bash":
        if call_name == "local_shell":
            action = arguments.get("action") if isinstance(arguments.get("action"), dict) else {}
            command_value = local_shell_command_to_string(action.get("command"))
        else:
            command_value = str(arguments.get("command") or "")
        patterns = _normalized_command_patterns(command_value)
        always = list(patterns)
    else:
        patterns = _path_patterns_for_call(str(getattr(call, "name", "") or ""), arguments, session)
        if not patterns:
            patterns = ["*"]
        always = list(patterns)

    ruleset = effective_ruleset(session, policy)
    for pattern in patterns:
        rule = evaluate(permission, pattern, ruleset)
        action = str(rule.get("action") or "ask")
        if action == "deny":
            return PermissionDecision(
                status="deny",
                permission=permission,
                patterns=patterns,
                always=always,
                reason=f"权限规则禁止执行 {permission}: {pattern}",
            )

    external_patterns = (
        _external_patterns(patterns, session)
        if (
            permission in {"read", "list", "glob", "grep", "edit"}
            and all(pattern != "*" for pattern in patterns)
            and not targets_plan_file_only(call_name, arguments, session)
        )
        else []
    )
    if external_patterns:
        for pattern in external_patterns:
            rule = evaluate("external_directory", pattern, ruleset)
            action = str(rule.get("action") or "ask")
            if action == "deny":
                return PermissionDecision(
                    status="deny",
                    permission="external_directory",
                    patterns=external_patterns,
                    always=external_patterns,
                    reason=f"权限规则禁止访问项目边界外目录: {pattern}",
                )
            if action == "ask":
                request = (
                    create_request(
                        session_id=session_id,
                        project_id=project_id,
                        permission="external_directory",
                        patterns=external_patterns,
                        metadata={
                            "tool": getattr(call, "name", ""),
                            "arguments": arguments,
                        },
                        always=external_patterns,
                        tool={"callID": getattr(call, "id", ""), "messageID": ""},
                    )
                    if create_pending_request
                    else None
                )
                return PermissionDecision(
                    status="ask",
                    permission="external_directory",
                    patterns=external_patterns,
                    always=external_patterns,
                    request=request,
                )

    for pattern in patterns:
        rule = evaluate(permission, pattern, ruleset)
        action = str(rule.get("action") or "ask")
        if action == "ask":
            metadata = {
                "tool": call_name,
                "arguments": arguments,
            }
            if call_name == "plan_exit":
                metadata["title"] = plan_exit_confirmation_text(session)
                metadata["description"] = metadata["title"]
            request = (
                create_request(
                    session_id=session_id,
                    project_id=project_id,
                    permission=permission,
                    patterns=patterns,
                    metadata=metadata,
                    always=always,
                    tool={"callID": getattr(call, "id", ""), "messageID": ""},
                )
                if create_pending_request
                else None
            )
            return PermissionDecision(
                status="ask",
                permission=permission,
                patterns=patterns,
                always=always,
                request=request,
            )

    return PermissionDecision(
        status="allow",
        permission=permission,
        patterns=patterns,
        always=always,
    )
