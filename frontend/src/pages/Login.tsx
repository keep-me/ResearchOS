/**
 * ResearchOS - 登录页面
 * @author Color2333
 */
import { useState } from "react";
import { Lock, Loader2, Eye, EyeOff, Sparkles, ArrowRight } from "lucide-react";
import { authApi } from "@/services/api";

interface LoginPageProps {
  onLoginSuccess: () => void;
}

export default function LoginPage({ onLoginSuccess }: LoginPageProps) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [showPassword, setShowPassword] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!password.trim()) {
      setError("请输入密码");
      return;
    }

    setLoading(true);
    setError("");

    try {
      const result = await authApi.login(password);
      sessionStorage.setItem("auth_token", result.access_token);
      onLoginSuccess();
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败，请重试");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-[linear-gradient(180deg,#fbfaf7_0%,#f5f2eb_100%)] px-3 py-6 sm:px-4 sm:py-10">
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_top_left,rgba(196,102,60,0.12),transparent_24%),radial-gradient(circle_at_bottom_right,rgba(217,119,6,0.10),transparent_18%)]" />
      <div className="relative grid w-full max-w-6xl overflow-hidden rounded-[28px] border border-border/70 bg-white/92 shadow-[0_40px_120px_-56px_rgba(15,23,35,0.22)] backdrop-blur-2xl sm:rounded-[36px] lg:grid-cols-[1.05fr_0.95fr]">
        <div className="border-b border-border/70 p-5 sm:p-7 lg:border-b-0 lg:border-r lg:p-10">
          <div className="mb-8 inline-flex items-center gap-3 rounded-full border border-border/70 bg-primary/5 px-4 py-2 text-xs font-semibold uppercase tracking-[0.24em] text-primary">
            <Sparkles className="h-4 w-4 text-primary" />
            ResearchOS
          </div>

          <h1 className="max-w-xl text-3xl font-extrabold tracking-[-0.06em] text-ink sm:text-4xl">
            把论文采集、分析、写作和沉淀放在同一个研究桌面里。
          </h1>
        </div>

        <div className="p-5 sm:p-7 lg:p-10">
          <div className="rounded-[24px] border border-border/70 bg-white p-5 shadow-[0_28px_70px_-42px_rgba(15,23,35,0.24)] sm:rounded-[32px] sm:p-6">
            <div className="mb-8">
              <div className="mb-4 inline-flex h-14 w-14 items-center justify-center rounded-[20px] bg-primary/10">
                <Lock className="h-7 w-7 text-primary" />
              </div>
              <h2 className="text-2xl font-bold tracking-[-0.04em] text-ink">进入 ResearchOS</h2>
            </div>

            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="relative">
                <input
                  type={showPassword ? "text" : "password"}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="访问密码"
                  className="h-12 w-full rounded-[20px] border border-border bg-page/70 px-4 pr-12 text-sm text-ink placeholder:text-ink-placeholder focus:border-primary/35 focus:outline-none focus:ring-4 focus:ring-primary/10"
                  disabled={loading}
                  autoFocus
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(!showPassword)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-ink-tertiary transition-colors hover:text-ink-secondary"
                >
                  {showPassword ? <EyeOff className="h-5 w-5" /> : <Eye className="h-5 w-5" />}
                </button>
              </div>

              {error && (
                <p className="rounded-2xl border border-red-400/20 bg-red-50 px-4 py-3 text-sm text-red-600">{error}</p>
              )}
              <button
                type="submit"
                disabled={loading}
                className="inline-flex h-12 w-full items-center justify-center gap-2 rounded-[20px] bg-[linear-gradient(135deg,var(--color-primary),var(--color-primary-hover))] text-sm font-semibold text-white shadow-lg shadow-primary/20 transition-all hover:-translate-y-0.5 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {loading ? (
                  <>
                    <Loader2 className="h-5 w-5 animate-spin" />
                    <span>验证中...</span>
                  </>
                ) : (
                  <>
                    <span>进入系统</span>
                    <ArrowRight className="h-4 w-4" />
                  </>
                )}
              </button>
            </form>
          </div>
        </div>
      </div>
    </div>
  );
}
