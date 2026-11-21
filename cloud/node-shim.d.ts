// The little of Node that cloudflare.config.ts uses; the project carries no @types/node.
declare module "node:fs" {
  export function readFileSync(path: string, encoding: "utf8"): string;
}
declare const process: { env: Record<string, string | undefined> };
