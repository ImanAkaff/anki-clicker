// Vite turns the React source in src/ into plain files a browser can run.
// The output goes straight into the add-on, at web/stats/, which is where
// Anki's built-in browser loads it from.
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // "./" makes every file reference relative, so the page works from
  // Anki's address (/_addons/anki_clicker/web/stats/) without knowing it.
  base: "./",
  build: {
    outDir: "../addon/anki_clicker/web/stats",
    emptyOutDir: true,
  },
});
