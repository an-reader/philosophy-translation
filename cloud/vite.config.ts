import { defineConfig } from "vite";
import { cloudflare } from "@cloudflare/vite-plugin";

// The static page (index.html, a copy of app.html) is written by `python3 tr.py cloud`.
export default defineConfig({
  publicDir: ".build/public",
  plugins: [cloudflare()],
});
