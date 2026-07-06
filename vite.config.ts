import { defineConfig } from "vite-plus";
import react from "@vitejs/plugin-react";

export default defineConfig({
  root: "rhythmflow/webui/frontend",
  base: "./",
  plugins: [react()],
  build: {
    outDir: "../frontend_dist",
    emptyOutDir: true,
    chunkSizeWarningLimit: 1500,
  },
  server: {
    port: 5173,
    strictPort: true,
  },
  test: {
    passWithNoTests: true,
  },
  staged: {
    "*": "vp check --fix",
  },
  fmt: {
    ignorePatterns: [".agents/**", "build/**", "dist/**", "rhythmflow/webui/frontend_dist/**"],
  },
  lint: {
    ignorePatterns: [".agents/**", "build/**", "dist/**", "rhythmflow/webui/frontend_dist/**"],
    jsPlugins: [{ name: "vite-plus", specifier: "vite-plus/oxlint-plugin" }],
    rules: { "vite-plus/prefer-vite-plus-imports": "error" },
    options: { typeAware: true, typeCheck: true },
  },
});
