import { SettingsDialog } from "@/components/SettingsDialog";

export default function SettingsPage() {
  return (
    <section className="space-y-4">
      <div className="rounded-xl border border-border bg-white px-5 py-5 lg:px-6">
        <h1 className="text-2xl font-semibold text-ink">系统配置</h1>
      </div>
      <SettingsDialog embedded />
    </section>
  );
}
