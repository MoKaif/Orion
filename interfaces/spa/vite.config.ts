import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Built assets are served by FastAPI from interfaces/spa/dist. During `pnpm dev`, API calls
// proxy to the running Orion backend (run.py on :8000; override with ORION_API).
const API = process.env.ORION_API || "http://127.0.0.1:8000";
const proxy = Object.fromEntries(
  ["/api", "/reviews", "/jobs", "/chat", "/confirm", "/sessions", "/plugins", "/ingest",
   "/health", "/tools", "/specialists", "/types"].map((p) => [p, { target: API, changeOrigin: true }]),
);

export default defineConfig({
  plugins: [react()],
  build: { outDir: "dist", emptyOutDir: true },
  server: { proxy },
});
