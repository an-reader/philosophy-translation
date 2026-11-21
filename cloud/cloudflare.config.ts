import { readFileSync } from "node:fs";
import { bindings, defineConfig } from "cf/config";
import * as entrypoint from "./src/index.ts" with { type: "cf-worker" };

// Local development (`cf dev`, `cf d1 ... --local`) uses this fixed id, so the loader and the Worker share one local database.
export const LOCAL_DATABASE_ID = "00000000-0000-4000-8000-000000000001";

type Target = { databaseId: string };

// The account's own values (the database ids) live in deploy.json, which git ignores: the repository is public. Other keys in it are ignored.
// The login key is not here: it is a Worker secret, ACCESS_KEY, set by the owner on each Worker.
// Copy deploy.example.json to deploy.json and fill it in. `cf` runs from cloud/, so the path is relative to it;
// DEPLOY_JSON names another file (tests).
function target(name: "real" | "preview"): Target {
  const file = process.env.DEPLOY_JSON || "deploy.json";
  let all: Record<string, Partial<Target> | undefined>;
  try {
    all = JSON.parse(readFileSync(file, "utf8"));
  } catch (e) {
    throw new Error(`Cannot build the "${name}" target: ${file} is missing or not JSON (copy deploy.example.json to deploy.json and fill it in). ${e}`);
  }
  const t = all[name] ?? {};
  // Fail closed: an empty database id would quietly create a new, empty database.
  if (!String(t.databaseId ?? "").trim()) throw new Error(`Cannot build the "${name}" target: "databaseId" is empty in ${file}.`);
  return { databaseId: t.databaseId!.trim() };
}

// One project, two Workers: a mode picks one complete configuration, and a mode deploys a separate Worker only when its
// config returns a different `name`. `cf deploy` is the real Worker (mode "production", the default for builds);
// `cf deploy --mode preview` is philosophy-translation-preview, with its own database. `cf dev` is mode "development".
export default defineConfig(({ mode, isPreview }) => {
  if (isPreview) throw new Error("Use `cf deploy --mode preview` for the preview Worker; `cf previews deploy` would reuse the real database.");
  const local = mode === "development";
  const devKey = local ? (process.env.TR_DEV_ACCESS_KEY ?? "") : "";
  let name = "philosophy-translation";
  let t: Target = { databaseId: LOCAL_DATABASE_ID };
  if (mode === "preview") {
    name = "philosophy-translation-preview";
    t = target("preview");
  } else if (mode === "production") {
    t = target("real");
  } else if (!local && mode !== undefined) {
    // No mode at all is how API commands such as `cf d1 ... --local` read this file; a build always has one.
    throw new Error(`Unknown mode ${JSON.stringify(mode)}: use development (cf dev), production (the default of a build) or preview.`);
  }
  return {
    worker: {
      name,
      compatibilityDate: "2026-11-01",
      entrypoint,
      // The page is a static asset; only /api/* runs the Worker, and it checks the login key first.
      assets: { runWorkerFirst: ["/api/*"] },
      env: {
        DB: bindings.d1({ name, id: t.databaseId }),
        // Skips the key login for local development only: "1" when `cf dev` evaluates this file (mode "development"),
        // empty in every build and deploy, and honoured by the Worker only on a loopback address besides.
        // TR_DEV_ACCESS_KEY (development mode only, for the end-to-end test of the login) turns the bypass off and gives the
        // Worker that key; a build or deploy never reads it, so no deploy can carry a key or a bypass from it.
        ACCESS_DEV_BYPASS: bindings.text(local && !devKey ? "1" : ""),
        ...(devKey ? { ACCESS_KEY: bindings.text(devKey) } : {}),
        // Timestamps in the repository are local time at this offset from UTC.
        TZ_OFFSET_HOURS: bindings.text("7"),
      },
    },
  };
});
