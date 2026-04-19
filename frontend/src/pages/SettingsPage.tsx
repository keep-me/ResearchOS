import { SettingsDialog } from "@/components/SettingsDialog";

export default function SettingsPage() {
  return (
    <section className="space-y-4 sm:space-y-5">
      <div className="page-hero rounded-[28px] p-4 sm:p-6 lg:rounded-[34px] lg:p-7">
        <div className="flex flex-col gap-2">
          <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-primary/80">
            System Settings
          </p>
          <h1 className="text-2xl font-semibold tracking-[-0.04em] text-ink sm:text-3xl">系统配置</h1>
          <p className="max-w-2xl text-sm leading-6 text-ink-secondary">
            统一管理模型、Skills、工作区、ACP 和 MCP 服务。手机端会自动切换成分段纵向布局，避免侧栏和内容区互相挤压。
          </p>
        </div>
      </div>
      <SettingsDialog embedded />
    </section>
  );
}
