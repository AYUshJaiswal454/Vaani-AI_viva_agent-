// Bundles src/ into ../app/static (served by the Python app). Works without a local node_modules if the
// packages are installed globally (falls back to known global paths).
import { build, context } from "esbuild";
import { cpSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const out = join(here, "..", "app", "static");
const watch = process.argv.includes("--watch");
const nodePaths = [join(here, "node_modules"), "/opt/npm-tools/node_modules", join(process.env.HOME || "", ".npm-global/lib/node_modules"),
  "/home/claude/.npm-global/lib/node_modules", "/usr/lib/node_modules", "/usr/local/lib/node_modules"].filter((p) => existsSync(p));

mkdirSync(join(out, "assets"), { recursive: true });
cpSync(join(here, "public", "fonts"), join(out, "assets", "fonts"), { recursive: true });
cpSync(join(here, "public", "favicon.svg"), join(out, "favicon.svg"));
cpSync(join(here, "public", "mic-worklet.js"), join(out, "assets", "mic-worklet.js"));

const options = {
  entryPoints: { app: join(here, "src", "main.tsx") },
  bundle: true,
  outdir: join(out, "assets"),
  entryNames: "[name]",
  format: "esm",
  target: ["es2020", "chrome100", "safari15", "firefox100"],
  minify: !watch,
  sourcemap: watch ? "inline" : false,
  jsx: "automatic",
  external: ["/assets/*"],
  nodePaths,
  loader: { ".css": "css" },
  define: { "process.env.NODE_ENV": JSON.stringify(watch ? "development" : "production") },
  logLevel: "info",
};

function writeIndex() {
  const version = Date.now().toString(36);
  const html = readFileSync(join(here, "public", "index.html"), "utf8").replaceAll("__V__", version);
  writeFileSync(join(out, "index.html"), html);
}

if (watch) {
  const ctx = await context(options);
  writeIndex();
  await ctx.watch();
  console.log("watching…");
} else {
  await build(options);
  writeIndex();
}
