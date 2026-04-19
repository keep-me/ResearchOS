import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { assistantSkillApi } from "@/services/api";
import type { AgentMode, AgentReasoningLevel, AssistantSkillItem, AssistantSkillRoot } from "@/types";

export type AgentPermissionPreset = "confirm" | "full_access" | "custom";

interface AgentWorkbenchCtxValue {
  permissionPreset: AgentPermissionPreset;
  reasoningLevel: AgentReasoningLevel;
  agentMode: AgentMode;
  assistantBackendId: string;
  activeSkillIds: string[];
  activeSkills: AssistantSkillItem[];
  availableSkills: AssistantSkillItem[];
  skillRoots: AssistantSkillRoot[];
  skillsLoading: boolean;
  skillsError: string | null;
  setPermissionPreset: (preset: AgentPermissionPreset) => void;
  setReasoningLevel: (level: AgentReasoningLevel) => void;
  setAgentMode: (mode: AgentMode) => void;
  setAssistantBackendId: (backendId: string) => void;
  refreshSkills: () => Promise<void>;
  toggleSkill: (skillId: string) => void;
  replaceSkills: (skillIds: string[]) => void;
  clearSkills: () => void;
}

const PERMISSION_PRESET_KEY = "researchos.agent.permissionPreset";
const REASONING_LEVEL_KEY = "researchos.agent.reasoningLevel";
const AGENT_MODE_KEY = "researchos.agent.mode";
const ASSISTANT_BACKEND_KEY = "researchos.agent.backendId";
const ACTIVE_SKILLS_KEY = "researchos.agent.activeSkillIds";
const DEFAULT_ASSISTANT_BACKEND_ID = "native";

function normalizeAssistantBackendId(value: string | null | undefined): string {
  const raw = String(value || "").trim().toLowerCase();
  if (!raw || raw === "researchos_native" || raw === "claw") {
    return DEFAULT_ASSISTANT_BACKEND_ID;
  }
  return raw;
}

const Ctx = createContext<AgentWorkbenchCtxValue | null>(null);

function readJson<T>(key: string, fallback: T): T {
  if (typeof window === "undefined") return fallback;
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return fallback;
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

function writeJson(key: string, value: unknown) {
  if (typeof window === "undefined") return;
  localStorage.setItem(key, JSON.stringify(value));
}

function readString<T extends string>(key: string, fallback: T): T {
  if (typeof window === "undefined") return fallback;
  const raw = localStorage.getItem(key);
  return (raw as T) || fallback;
}

export function AgentWorkbenchProvider({ children }: { children: ReactNode }) {
  const [permissionPreset, setPermissionPresetState] = useState<AgentPermissionPreset>(() => {
    const stored = readString<AgentPermissionPreset>(PERMISSION_PRESET_KEY, "confirm");
    return stored === "confirm" || stored === "full_access" || stored === "custom" ? stored : "confirm";
  });
  const [reasoningLevel, setReasoningLevelState] = useState<AgentReasoningLevel>(() => {
    const stored = readString<AgentReasoningLevel>(REASONING_LEVEL_KEY, "default");
    return stored === "default" || stored === "low" || stored === "medium" || stored === "high" || stored === "xhigh"
      ? stored
      : "default";
  });
  const [agentMode, setAgentModeState] = useState<AgentMode>(() => {
    const stored = readString<AgentMode>(AGENT_MODE_KEY, "build");
    return stored === "plan" ? "plan" : "build";
  });
  const [assistantBackendId, setAssistantBackendIdState] = useState<string>(() =>
    normalizeAssistantBackendId(readString<string>(ASSISTANT_BACKEND_KEY, DEFAULT_ASSISTANT_BACKEND_ID)),
  );
  const [activeSkillIds, setActiveSkillIds] = useState<string[]>(() =>
    readJson<string[]>(ACTIVE_SKILLS_KEY, []),
  );
  const [availableSkills, setAvailableSkills] = useState<AssistantSkillItem[]>([]);
  const [skillRoots, setSkillRoots] = useState<AssistantSkillRoot[]>([]);
  const [skillsLoading, setSkillsLoading] = useState(false);
  const [skillsError, setSkillsError] = useState<string | null>(null);

  useEffect(() => {
    if (typeof window !== "undefined") {
      localStorage.setItem(PERMISSION_PRESET_KEY, permissionPreset);
    }
  }, [permissionPreset]);

  useEffect(() => {
    if (typeof window !== "undefined") {
      localStorage.setItem(REASONING_LEVEL_KEY, reasoningLevel);
    }
  }, [reasoningLevel]);

  useEffect(() => {
    if (typeof window !== "undefined") {
      localStorage.setItem(AGENT_MODE_KEY, agentMode);
    }
  }, [agentMode]);

  useEffect(() => {
    if (typeof window !== "undefined") {
      localStorage.setItem(ASSISTANT_BACKEND_KEY, assistantBackendId);
    }
  }, [assistantBackendId]);

  useEffect(() => {
    writeJson(ACTIVE_SKILLS_KEY, activeSkillIds);
  }, [activeSkillIds]);

  const refreshSkills = useCallback(async () => {
    setSkillsLoading(true);
    try {
      const result = await assistantSkillApi.list();
      const items = result.items || [];
      const roots = result.roots || [];
      setSkillsError(null);
      setAvailableSkills(items);
      setSkillRoots(roots);
      setActiveSkillIds((prev) => prev.filter((id) => items.some((skill) => skill.id === id)));
    } catch (error) {
      const message = error instanceof Error ? error.message : "Skills 扫描失败";
      setAvailableSkills([]);
      setSkillRoots([]);
      if (message.includes("404")) {
        setSkillsError("当前本地后端还没有 `/settings/assistant-skills` 接口。请重启到最新后端代码后再重新扫描。");
      } else {
        setSkillsError(message);
      }
    } finally {
      setSkillsLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshSkills();
  }, []);

  const activeSkills = useMemo(
    () => availableSkills.filter((skill) => activeSkillIds.includes(skill.id)),
    [activeSkillIds, availableSkills],
  );

  const value = useMemo<AgentWorkbenchCtxValue>(() => ({
    permissionPreset,
    reasoningLevel,
    agentMode,
    assistantBackendId,
    activeSkillIds,
    activeSkills,
    availableSkills,
    skillRoots,
    skillsLoading,
    skillsError,
    setPermissionPreset: setPermissionPresetState,
    setReasoningLevel: setReasoningLevelState,
    setAgentMode: setAgentModeState,
    setAssistantBackendId: (backendId: string) => setAssistantBackendIdState(normalizeAssistantBackendId(backendId)),
    refreshSkills,
    toggleSkill: (skillId: string) => {
      setActiveSkillIds((prev) =>
        prev.includes(skillId) ? prev.filter((id) => id !== skillId) : [...prev, skillId],
      );
    },
    replaceSkills: (skillIds: string[]) => setActiveSkillIds(skillIds),
    clearSkills: () => setActiveSkillIds([]),
  }), [permissionPreset, reasoningLevel, agentMode, assistantBackendId, activeSkillIds, activeSkills, availableSkills, skillRoots, skillsLoading, skillsError]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAgentWorkbench() {
  const ctx = useContext(Ctx);
  if (!ctx) {
    throw new Error("useAgentWorkbench must be used inside AgentWorkbenchProvider");
  }
  return ctx;
}

export function buildAgentWorkbenchPrelude(input: {
  workspace: { path: string; title: string } | null;
  permissionPreset: AgentPermissionPreset;
  reasoningLevel: AgentReasoningLevel;
  agentMode?: AgentMode;
  activeSkills: AssistantSkillItem[];
}): string {
  const lines: string[] = [];
  if (input.workspace?.path) {
    lines.push(`当前工作区：${input.workspace.title}（${input.workspace.path}）`);
  } else {
    lines.push("当前工作区：默认聊天（未绑定本地目录）");
  }
  lines.push(
    `权限预设：${
      input.permissionPreset === "full_access"
        ? "自动确认权限请求，适合连续执行任务。"
        : input.permissionPreset === "custom"
          ? "自定义策略，沿用系统设置中的细粒度执行规则。"
          : "需要确认权限请求，重要操作不会自动放行。"
    }`,
  );
  lines.push(
    `推理程度：${
      input.reasoningLevel === "default"
        ? "默认，使用当前模型的标准推理档位。"
        : input.reasoningLevel === "xhigh"
        ? "超高，尽量做完整检索、交叉验证和更深入推理。"
        : input.reasoningLevel === "high"
        ? "高，先做更完整的分析与交叉检查。"
        : input.reasoningLevel === "low"
          ? "低，优先快速给出结论与下一步。"
          : "中，保持分析充分但不过度展开。"
    }`,
  );
  if (input.agentMode) {
    lines.push(`当前 agent 模式：${input.agentMode}`);
  }
  if (input.activeSkills.length > 0) {
    lines.push("已启用 Skills：");
    for (const skill of input.activeSkills) {
      lines.push(`- ${skill.name}（${skill.path}）：${skill.description || "遵循该技能目录中的工作流和约束。"}`);
    }
  }
  return lines.join("\n");
}
