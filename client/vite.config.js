import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// Tauri 在开发时通过固定端口加载前端，因此这里禁止自动切换端口
export default defineConfig({
  plugins: [vue()],
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    // 浏览器预览模式下，把 /api 转发到本地预览后端
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8971",
        changeOrigin: true,
      },
    },
  },
  envPrefix: ["VITE_", "TAURI_"],
  build: {
    target: "es2021",
    minify: "esbuild",
    sourcemap: false,
    chunkSizeWarningLimit: 900,
  },
});
