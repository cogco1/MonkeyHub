/** Generate/check both API clients against their real FastAPI schemas. */
import { spawnSync } from "node:child_process";
import { existsSync, readFileSync, readdirSync, rmSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
const web = dirname(dirname(fileURLToPath(import.meta.url)));
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
run(process.env.PYTHON ?? "python", [resolve(web, "scripts/dump-openapi.py")]);
const drift = [];
// Each client beside the directory of its schema. The project-runtime schema names no
// server, so its client's default baseUrl comes from the input path's first segment:
// both schemas stay under .generated/.
for (const [schemas, client] of [[".generated/project-runtime", "src/api/project-runtime/generated"], [".generated", "src/api/generated"]]) {
  const committed = resolve(web, client);
  const scratch = resolve(web, schemas, "api-check");
  if (!write) rmSync(scratch, { recursive: true, force: true });
  const generator = resolve(web, "tools/openapi-ts/node_modules/@hey-api/openapi-ts/bin/run.js");
  run(process.execPath, [generator, "-i", `${schemas}/openapi.json`, "-o", write ? client : `${schemas}/api-check`]);
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
