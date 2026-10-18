import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// All /api calls are proxied to the FastAPI backend — no keys in the browser.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
