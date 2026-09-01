import { resolve } from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The engine serves this bundle itself in production (one process, one port),
// so `dist/` is committed and the booth host needs no toolchain. In dev the
// Vite server owns the page and proxies the engine's two surfaces — `/api` for
// login and history, `/ws` for the telemetry socket — through to it.
const ENGINE = process.env.PIZZA_ENGINE_URL ?? "http://127.0.0.1:8080";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    // Two pages, not an SPA route: the login page must be servable to a
    // browser that has no session yet, without shipping it the console.
    rollupOptions: {
      input: {
        main: resolve(__dirname, "index.html"),
        login: resolve(__dirname, "login.html"),
      },
    },
  },
  server: {
    proxy: {
      "/api": { target: ENGINE, changeOrigin: true },
      "/ws": { target: ENGINE, ws: true, changeOrigin: true },
    },
  },
});
