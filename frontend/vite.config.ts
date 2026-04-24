/**
 * ResearchOS Frontend - Vite Configuration
 * @author Color2333
 */
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import svgr from "vite-plugin-svgr";
import path from "path";
import fs from "node:fs";

function copyMermaidChunksPlugin() {
  return {
    name: "copy-mermaid-esm-chunks",
    apply: "build" as const,
    closeBundle() {
      const sourceDir = path.resolve(__dirname, "node_modules/mermaid/dist/chunks/mermaid.esm.min");
      const targetDir = path.resolve(__dirname, "dist/assets/chunks/mermaid.esm.min");
      if (!fs.existsSync(sourceDir)) return;
      fs.rmSync(targetDir, { recursive: true, force: true });
      fs.mkdirSync(path.dirname(targetDir), { recursive: true });
      fs.cpSync(sourceDir, targetDir, { recursive: true });
    },
  };
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const envValue = (key: string) => process.env[key] ?? env[key];
  const port = Number(envValue("VITE_PORT") || 5173);
  const usePolling = envValue("VITE_USE_POLLING") === "true";
  const pollInterval = Number(envValue("VITE_POLL_INTERVAL") || 300);
  const hmrPort = Number(envValue("VITE_HMR_PORT") || port);
  const hmrHost = envValue("VITE_HMR_HOST");
  const proxyTarget = envValue("VITE_PROXY_TARGET");
  const forceOptimizeDeps = envValue("VITE_FORCE_OPTIMIZE_DEPS") === "true";

  return {
    plugins: [react(), tailwindcss(), svgr(), copyMermaidChunksPlugin()],
    resolve: {
      alias: [
        { find: "@", replacement: path.resolve(__dirname, "./src") },
      ],
    },
    server: {
      port,
      host: "0.0.0.0",
      strictPort: true,
      watch: usePolling
        ? {
            usePolling: true,
            interval: pollInterval,
          }
        : undefined,
      hmr: hmrHost
        ? {
            host: hmrHost,
            port: hmrPort,
            clientPort: hmrPort,
          }
        : undefined,
      proxy: proxyTarget
        ? {
            "/api": {
              target: proxyTarget,
              changeOrigin: true,
              secure: false,
              ws: true,
              rewrite: (requestPath) => requestPath.replace(/^\/api/, ""),
            },
          }
        : undefined,
    },
    optimizeDeps: {
      include: [
        // The graph route is lazy-loaded. Pre-bundle these deps up front so the
        // first visit to /graph does not trigger a late optimize pass that can
        // return "Outdated Optimize Dep" for react-force-graph-2d.
        "react-force-graph-2d",
        "force-graph",
        "prop-types",
      ],
      // Keep this opt-in. Forcing a re-optimize on every dev boot can race with
      // early lazy-route requests and produce transient "Outdated Optimize Dep"
      // failures in host smoke / desktop cold starts.
      force: mode === "development" && forceOptimizeDeps,
    },
    build: {
      modulePreload: true,
      reportCompressedSize: true,
      rollupOptions: {
        output: {
          manualChunks(id) {
            // React 核心
            if (id.includes("node_modules/react/") || id.includes("node_modules/react-dom/") || id.includes("node_modules/react-router-dom/") || id.includes("node_modules/scheduler/")) {
              return "react-vendor";
            }
            // KaTeX 单独切（体积最大，且只有 LaTeX 内容才用到）
            if (id.includes("node_modules/katex/")) {
              return "katex";
            }
            // Markdown 解析器（不含 katex）
            if (id.includes("node_modules/react-markdown/") || id.includes("node_modules/remark") || id.includes("node_modules/rehype") || id.includes("node_modules/unified/") || id.includes("node_modules/mdast") || id.includes("node_modules/hast") || id.includes("node_modules/micromark") || id.includes("node_modules/vfile") || id.includes("node_modules/bail/") || id.includes("node_modules/is-plain-obj/") || id.includes("node_modules/trough/") || id.includes("node_modules/extend/")) {
              return "markdown";
            }
            // 图标库
            if (id.includes("node_modules/lucide-react/")) {
              return "icons";
            }
            // D3 / 图谱
            if (id.includes("node_modules/d3") || id.includes("node_modules/@nivo") || id.includes("node_modules/force-graph") || id.includes("node_modules/three/")) {
              return "graph-vendor";
            }
            // DOMPurify
            if (id.includes("node_modules/dompurify/")) {
              return "dompurify";
            }
          },
        },
      },
      chunkSizeWarningLimit: 600,
    },
  };
});
