/**
 * Graph Explorer - 研究洞察（2 大面板：全局概览 / 领域洞察）
 * @author Bamzc
 */
import { useState } from "react";
import { Compass, TrendingUp } from "lucide-react";
import { Tabs } from "@/components/ui";
import OverviewPanel from "@/components/graph/OverviewPanel";
import InsightPanel from "@/components/graph/InsightPanel";

const TABS = [
  { id: "overview", label: "全局概览", icon: Compass },
  { id: "insight", label: "领域洞察", icon: TrendingUp },
] as const;

type TabId = (typeof TABS)[number]["id"];

export default function GraphExplorer() {
  const [activeTab, setActiveTab] = useState<TabId>("overview");
  const tabs = TABS.map((tab) => ({
    id: tab.id,
    label: (
      <span className="inline-flex items-center gap-2">
        <tab.icon className="h-4 w-4" />
        {tab.label}
      </span>
    ),
  }));

  return (
    <div className="animate-fade-in space-y-7">
      {/* 页面头 */}
      <div className="page-hero rounded-[34px] p-6 lg:p-7">
        <div className="flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
          <div className="flex items-start gap-4">
            <div className="glass-segment flex h-12 w-12 items-center justify-center rounded-[20px]">
              <Compass className="h-5 w-5 text-primary" />
            </div>
            <div>
              <span className="inline-flex items-center gap-2 rounded-full border border-primary/12 bg-primary/6 px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.22em] text-primary">
                Research Insights
              </span>
              <h1 className="mt-3 text-2xl font-bold tracking-[-0.045em] text-ink">研究洞察</h1>
            </div>
          </div>
        </div>
      </div>

      {/* 功能标签 — 2 个大 tab */}
      <Tabs tabs={tabs} active={activeTab} onChange={(value) => setActiveTab(value as TabId)} className="w-full" />

      {/* 面板内容 */}
      {activeTab === "overview" && <OverviewPanel />}
      {activeTab === "insight" && <InsightPanel />}
    </div>
  );
}
