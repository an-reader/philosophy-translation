import { defineConfig } from "vite";
import { cloudflare } from "@cloudflare/vite-plugin";

// The static page (index.html, a copy of app.html) is written by `python3 tr.py cloud`.
// `cf dev` has no --persist-to flag, so a test names its own local-database folder here instead (parity.py does);
// unset, the database stays in .cloudflare/state.
const persist = process.env.CF_PERSIST_TO;

export default defineConfig({
  publicDir: ".build/public",
  plugins: [cloudflare(persist ? { persistState: { path: persist } } : {})],
});
