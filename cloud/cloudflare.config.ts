import { bindings, defineConfig } from "cf/config";
import * as entrypoint from "./src/index.ts" with { type: "cf-worker" };

// The id `cf d1 create` prints for the deployed database; the owner fills it in before the first deploy.
const DATABASE_ID = "";
// Local development (`cf dev`, `cf d1 ... --local`) uses this fixed id, so the loader and the Worker share one local database.
export const LOCAL_DATABASE_ID = "00000000-0000-4000-8000-000000000001";

export default defineConfig(({ mode }) => ({
  worker: {
    name: "philosophy-translation",
    compatibilityDate: "2026-11-01",
    entrypoint,
    // The page is a static asset; only /api/* runs the Worker, and it checks the Access login first.
    assets: { runWorkerFirst: ["/api/*"] },
    env: {
      DB: bindings.d1({ name: "philosophy-translation", id: mode === "development" ? LOCAL_DATABASE_ID : DATABASE_ID || undefined }),
      // Cloudflare Access: the owner fills both after creating the Access application. Empty, the API refuses every request.
      TEAM_DOMAIN: bindings.text(""), // https://<team>.cloudflareaccess.com
      POLICY_AUD: bindings.text(""), // the application's Application Audience (AUD) tag
      // Skips the Access login for local development only: "1" when `cf dev` evaluates this file (mode "development"),
      // empty in every build and deploy (mode "production" unless someone passes --mode development), and honoured
      // by the Worker only on a loopback address besides.
      ACCESS_DEV_BYPASS: bindings.text(mode === "development" ? "1" : ""),
      // Timestamps in the repository are local time at this offset from UTC.
      TZ_OFFSET_HOURS: bindings.text("7"),
    },
  },
}));
