/** Generate/check both API clients against their real FastAPI schemas. */
import { spawnSync } from "node:child_process";
import { existsSync, readFileSync, readdirSync, rmSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
const workspace = dirname(dirname(fileURLToPath(import.meta.url)));
const web = dirname(workspace);
const write = process.argv.includes("--write");
function run(command, args, cwd = web) {
  const result = spawnSync(command, args, { cwd, stdio: "inherit" });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status ?? 1);
}
function files(root) {
  if (!existsSync(root)) return [];
  return readdirSync(root, { withFileTypes: true }).flatMap((entry) =>
    entry.isDirectory() ? files(join(root, entry.name)).map((path) => `${entry.name}/${path}`) : [entry.name]);
}
run(process.env.PYTHON ?? "python", [resolve(workspace, "scripts/dump-openapi.py")]);
const drift = [];
for (const root of [workspace, web]) {
  const committed = resolve(root, "src/api/generated");
  const scratch = resolve(root, ".generated/api-check");
  if (!write) rmSync(scratch, { recursive: true, force: true });
  const generator = resolve(workspace, "tools/openapi-ts/node_modules/@hey-api/openapi-ts/bin/run.js");
  run(process.execPath, [generator, "-i", ".generated/openapi.json", "-o", write ? "src/api/generated" : ".generated/api-check"], root);
  if (write) continue;
  const expected = files(committed), actual = files(scratch);
  for (const path of new Set([...expected, ...actual])) {
    if (!expected.includes(path) || !actual.includes(path)
      || readFileSync(join(committed, path), "utf8").replace(/\r\n/g,"\n") !== readFileSync(join(scratch,path),"utf8").replace(/\r\n/g,"\n"))
      drift.push(relative(web, join(committed,path)));
  }
  rmSync(scratch, { recursive: true, force: true });
}
if (drift.length) { console.error("Generated API drift:", drift.join("\n")); process.exit(1); }
console.log(write ? "Generated Hub and project-runtime clients." : "Both API clients match their schemas.");
