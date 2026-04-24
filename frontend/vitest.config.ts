import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    include: ["tests/**/*.unit.{ts,tsx}", "tests/security.spec.tsx"],
    exclude: ["tests/smoke.spec.ts"],
    setupFiles: "./tests/setup.ts",
  },
});
