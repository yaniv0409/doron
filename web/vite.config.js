import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const rootDir = fileURLToPath(new URL(".", import.meta.url));

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "react-markdown": path.join(rootDir, "node_modules/react-markdown/lib/react-markdown.js"),
    },
  },
  server: {
    port: 5173,
  },
});
