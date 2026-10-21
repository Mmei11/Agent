import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/sessions": process.env.API_TARGET || "http://localhost:8000",
    },
  },
});
