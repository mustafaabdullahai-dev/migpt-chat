import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    host: true,
    allowedHosts: true, // allow ngrok / LAN hosts in dev
    proxy: {
      "/api": {
        // Backend for THIS project runs on 8100. Override with VITE_API_TARGET.
        target: process.env.VITE_API_TARGET || "http://localhost:8100",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});