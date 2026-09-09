import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Backend port is configurable (BACKEND_PORT) since 8000 is a common default
// that's easy to collide with on a dev machine running other projects.
const backendPort = process.env.BACKEND_PORT || 8000;

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: `http://localhost:${backendPort}`,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});

