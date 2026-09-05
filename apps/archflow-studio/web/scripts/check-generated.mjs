/**
 * `npm run api:check` — does the committed client still match the API?
 *
 * The generated client is committed so a build needs no network and no Python.
 * That only works if it is the client the current schema produces, so this
 * script dumps the schema again, regenerates into a throwaway directory beside
 * it, and compares the two trees file by file. Any difference is drift and
 * exits 1 with the paths.
 *
 * The regeneration uses the *same relative input path* as `api:generate`,
 * because the generator writes that path into the client's default base URL:
 * generating from somewhere else would report drift that is only about where
 * the file was read from.
 */

import { spawnSync } from "node:child_process";
import { existsSync, readFileSync, readdirSync, rmSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const WEB_DIR = dirname(dirname(fileURLToPath(import.meta.url)));
const COMMITTED = resolve(WEB_DIR, "src/api/generated");
const SCRATCH_REL = ".generated/api-check";
const SCRATCH = resolve(WEB_DIR, SCRATCH_REL);

// npm is a shell script on POSIX and a `.cmd` on Windows, which Node will only
// start through a shell — so the command goes as one line. Every argument this
// script passes is a literal it wrote itself; none comes from outside.
function run(args) {
  const line = `npm ${args.join(" ")}`;
  const result = spawnSync(line, { cwd: WEB_DIR, stdio: "inherit", shell: true });
  if (result.error) {
    console.error(`api:check — could not run \`${line}\`: ${result.error.message}`);
    process.exit(1);
  }
  if (result.status !== 0) {
    console.error(`api:check — \`${line}\` failed.`);
    process.exit(result.status ?? 1);
  }
}

function filesUnder(root) {
  const found = [];
  const walk = (directory) => {
    for (const entry of readdirSync(directory).sort()) {
      const full = join(directory, entry);
      if (statSync(full).isDirectory()) walk(full);
      else found.push(relative(root, full).split("\\").join("/"));
    }
  };
  if (existsSync(root)) walk(root);
  return found;
}

rmSync(SCRATCH, { recursive: true, force: true });
run(["run", "api:dump"]);
run([
  "--prefix",
  "tools/openapi-ts",
  "exec",
  "--",
  "openapi-ts",
  "-i",
  ".generated/openapi.json",
  "-o",
  SCRATCH_REL,
]);

const fresh = filesUnder(SCRATCH);
const committed = filesUnder(COMMITTED);
const drift = [];

for (const path of committed) {
  if (!fresh.includes(path)) drift.push(`only in src/api/generated: ${path}`);
}
for (const path of fresh) {
  if (!committed.includes(path)) {
    drift.push(`the schema now generates a file that is not committed: ${path}`);
    continue;
  }
  // A Windows checkout may use CRLF while the generator writes LF.
  const a = readFileSync(join(COMMITTED, path), "utf8").replace(/\r\n/g, "\n");
  const b = readFileSync(join(SCRATCH, path), "utf8").replace(/\r\n/g, "\n");
  if (a !== b) drift.push(`content differs: ${path}`);
}

rmSync(SCRATCH, { recursive: true, force: true });

if (drift.length > 0) {
  console.error(
    "api:check — the committed client no longer matches the API's schema:",
  );
  for (const line of drift) console.error(`  ${line}`);
  console.error("Run `npm run api:generate` and commit the result.");
  process.exit(1);
}

console.log(
  `api:check — ${committed.length} generated files match the current schema.`,
);
