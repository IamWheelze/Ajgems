import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dev server proxies /api to Django, so session cookies and CSRF work same-origin.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": "http://localhost:8000" } },
});
