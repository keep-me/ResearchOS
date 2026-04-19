import { useEffect } from "react";
import { BadgeCheck, Loader2, RefreshCw } from "lucide-react";
import { Modal } from "@/components/ui/Modal";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/utils";
import { useAssistantInstance } from "@/contexts/AssistantInstanceContext";

function skillSourceLabel(source: "codex" | "agents" | "project") {
  if (source === "project") return "项目";
  if (source === "codex") return "Codex";
  return "Agents";
}

export default function AgentSkillsDialog({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const {
    activeSkillIds,
    availableSkills,
    skillsLoading,
    skillsError,
    toggleSkill,
    clearSkills,
    refreshSkills,
    settingsScopeLabel,
  } = useAssistantInstance();

  useEffect(() => {
    if (!open) return;
    void refreshSkills();
  }, [open, refreshSkills]);

  return (
    <Modal open={open} onClose={onClose} title="Skills" maxWidth="xl" className="max-h-[90vh] overflow-hidden">
      <div className="flex max-h-[calc(90vh-7rem)] flex-col gap-4">
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-[22px] border border-border/70 bg-page/68 px-4 py-3">
          <div className="min-w-0">
            <p className="text-sm font-semibold text-ink">
              已启用 {activeSkillIds.length} / {availableSkills.length} 个 Skills
            </p>
          </div>

          <div className="flex gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => void refreshSkills()}
              disabled={skillsLoading}
            >
              {skillsLoading ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-1 h-4 w-4" />}
              重新扫描
            </Button>
            <Button variant="ghost" size="sm" onClick={clearSkills}>
              清空
            </Button>
            <Button variant="primary" size="sm" onClick={onClose}>
              完成
            </Button>
          </div>
        </div>

        {skillsError && (
          <div className="rounded-[22px] border border-warning/20 bg-warning-light px-4 py-4 text-sm leading-7 text-warning">
            {skillsError}
          </div>
        )}

        <div className="min-h-0 flex-1 overflow-y-auto pr-1">
          {skillsLoading ? (
            <div className="flex h-full min-h-[24rem] items-center justify-center gap-2 rounded-[22px] border border-border/70 bg-page/60 px-4 py-8 text-sm text-ink-secondary">
              <Loader2 className="h-4 w-4 animate-spin" />
              扫描中...
            </div>
          ) : availableSkills.length === 0 ? (
            <div className="flex h-full min-h-[24rem] items-center justify-center rounded-[22px] border border-dashed border-border/80 bg-page/45 px-4 py-8 text-center text-sm text-ink-secondary">
              暂无流程模板
            </div>
          ) : (
            <div className="grid gap-3 md:grid-cols-2">
              {availableSkills.map((skill) => {
                const active = activeSkillIds.includes(skill.id);
                return (
                  <button
                    key={skill.id}
                    type="button"
                    onClick={() => toggleSkill(skill.id)}
                    className={cn(
                      "rounded-[22px] border px-4 py-4 text-left transition",
                      active
                        ? "border-primary/20 bg-primary/8 shadow-[0_20px_40px_-36px_rgba(79,70,229,0.42)]"
                        : "border-border/75 bg-white/82 hover:border-primary/20 hover:bg-primary/6",
                    )}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <p className="text-sm font-semibold text-ink">{skill.name}</p>
                          <span className="rounded-full border border-border/75 bg-page/80 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-tertiary">
                            {skillSourceLabel(skill.source)}
                          </span>
                          {skill.system && (
                            <span className="rounded-full border border-border/75 bg-page/80 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-tertiary">
                              system
                            </span>
                          )}
                        </div>
                        <p className="mt-1 break-all text-[10px] text-ink-tertiary">
                          {skill.relative_path}
                          {settingsScopeLabel ? ` · ${settingsScopeLabel}` : ""}
                        </p>
                      </div>
                      {active && <BadgeCheck className="h-5 w-5 shrink-0 text-primary" />}
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </Modal>
  );
}
