import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  define: {
    // 构建时间戳：页脚显示，便于确认浏览器跑的是不是最新构建。
    __BUILD_ID__: JSON.stringify(
      new Date().toISOString().slice(0, 16).replace("T", " ") + "Z",
    ),
  },
  plugins: [react()],
  server: {
    proxy: {
      "/sessions": process.env.API_TARGET || "http://localhost:8000",
      "/auth": process.env.API_TARGET || "http://localhost:8000",
      "/me": process.env.API_TARGET || "http://localhost:8000",
    },
  },
});
