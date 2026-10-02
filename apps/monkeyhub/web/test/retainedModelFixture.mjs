import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { createServer } from "node:http";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Both retained-model suites own a disposable P036 project and real Runtime.
// The three tiny, committed 3DMs are public test fixtures, never a user's model.
export async function createRetainedModelFixture({ name = "retained-model" } = {}) {
  const webRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
  const repoRoot = path.resolve(webRoot, "../../..");
  const apiRoot = path.join(repoRoot, "services/project-runtime");
  const root = await mkdtemp(path.join(tmpdir(), `monkeyhub-${name}-`));
  const projectRoot = path.join(root, "demo-project");
  const python = process.env.PYTHON ?? "python";
  // Never inherit a developer's shared runtime, sync target, monitor directory,
  // renderer or provider configuration into this disposable service.
  const inherited = Object.fromEntries(Object.entries(process.env).filter(([key]) =>
    !/^(ARCHFLOW_|MONKEY|ANTHROPIC_|OPENAI_|AZURE_OPENAI_|GEMINI_|GOOGLE_API_KEY)/.test(key)));
  const env = { ...inherited, PYTHONUTF8: "1", PYTHONPATH: [repoRoot, path.join(apiRoot, "src")].join(path.delimiter) };
  let api, log = "", closed = false;
  async function close() {
    if (closed) return;
    closed = true;
    if (api && api.exitCode === null) {
      const exited = new Promise((resolve) => api.once("exit", resolve));
      api.kill("SIGTERM");
      await exited;
    }
    assert.equal(path.dirname(root), path.resolve(tmpdir()));
    assert.ok(path.basename(root).startsWith(`monkeyhub-${name}-`));
    await rm(root, { recursive: true, force: true });
  }
  try {
    const setup = spawnSync(python, ["-c", `
import copy, json, sys
import rhino3dm  # Fail clearly when monkeycad[inspection] was not installed.
from pathlib import Path
from tools.dev import source_roots
source_roots.put_first(Path(sys.argv[2]))
from tests import support
from tests.support import PROJECT_ID, REFERENCE_RUN_ID, RECORD_PAYLOAD, make_project, retain_runner_receipt, runner_state_digest
from tests.test_documents import two_page_pdf
repository, _ = make_project(Path(sys.argv[1]))
changed = copy.deepcopy(RECORD_PAYLOAD)
next(row for row in changed['entities'] if row['entity_id'] == 'portico-base')['fields']['params']['height'] = 0.8
run_b = repository.create_run('run-b')
digest_a = runner_state_digest(repository, REFERENCE_RUN_ID)
digest_b = runner_state_digest(repository, 'run-b', changed)
retain_runner_receipt(repository, run_b, design_state_digest=digest_b, record_payload=changed)
# Native export identity must name the same real design state as its run.
support.RHINO_DESIGN_STATE_DIGEST = digest_a
support.retain_rhino_receipt(repository, repository.load_run(REFERENCE_RUN_ID), stage_id='native', file_name='native-a.3dm',
    payload_bytes=Path('tests/fixtures/model-source-composed.3dm').read_bytes())
Path(sys.argv[1], 'pages.pdf').write_bytes(two_page_pdf())
print(json.dumps({'runA': REFERENCE_RUN_ID, 'digestA': digest_a, 'digestB': digest_b}))
`, root, repoRoot], { cwd: apiRoot, env, encoding: "utf8" });
    assert.equal(setup.status, 0, setup.stderr || setup.stdout);
    const { runA, digestA, digestB } = JSON.parse(setup.stdout.trim());
    const port = await new Promise((resolve, reject) => {
      const probe = createServer(); probe.once("error", reject);
      probe.listen(0, "127.0.0.1", () => { const { port } = probe.address(); probe.close(() => resolve(port)); });
    });
    const apiOrigin = `http://127.0.0.1:${port}`;
    async function startRuntime() {
      api = spawn(python, ["-m", "project_runtime.main", "--port", String(port), "--project-dir", projectRoot], {
        cwd: apiRoot, env: { ...env, ARCHFLOW_STUDIO_MODE: "local", ARCHFLOW_STUDIO_BIND: "127.0.0.1", ARCHFLOW_STUDIO_SERVICE_ROLE: "runtime",
        ARCHFLOW_STUDIO_RENDER_PROVIDER: "off", ARCHFLOW_STUDIO_REFERENCE_RUN: runA, ARCHFLOW_STUDIO_CAD_EXPORT: "off", ARCHFLOW_STUDIO_INTENT_PROVIDER: "deterministic", ARCHFLOW_STUDIO_CACHE_DIR: path.join(root, "cache") },
        stdio: ["ignore", "pipe", "pipe"],
      });
      api.on("error", (error) => { log += String(error); });
      for (const output of [api.stdout, api.stderr]) output.on("data", (data) => { log = (log + data).slice(-16000); });
      for (let attempt = 0; ; attempt++) {
        if (await fetch(`${apiOrigin}/api/health`).then((answer) => answer.ok).catch(() => false)) break;
        assert.ok(attempt < 3000 && api.exitCode === null, `Runtime startup failed: ${log}`);
        await new Promise((resolve) => setTimeout(resolve, 100));
      }
    }
    await startRuntime();
    async function request(method, endpoint, body) {
      const response = await fetch(new URL(endpoint, apiOrigin), { method, redirect: "error", signal: AbortSignal.timeout(30000),
        headers: body === undefined ? undefined : { "content-type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
      const value = await response.json();
      assert.ok(response.ok, `${method} ${endpoint}: ${response.status} ${JSON.stringify(value)}`);
      return value;
    }
    function getJson(endpoint, query = {}) {
      const url = new URL(endpoint, apiOrigin);
      for (const [key, value] of Object.entries(query)) if (value !== null && value !== undefined) url.searchParams.set(key, String(value));
      return request("GET", url);
    }
    const project = await getJson("/api/project");
    assert.equal(path.resolve(project.projectDir), projectRoot);
    const register = async (runId, stateDigest, filename) => request("POST", "/api/model-assets", {
      projectId: project.projectId, runId, stateDigest, fileName: filename,
      contentBase64: (await readFile(path.join(apiRoot, "tests/fixtures", filename))).toString("base64"),
    });
    const A = (await register(runA, digestA, "model-source-a.3dm")).modelSource;
    const B = (await register("run-b", digestB, "model-source-b.3dm")).modelSource;
    const alternateB = (await register("run-b", digestB, "model-source-composed.3dm")).modelSource;
    const stage = await request("POST", "/api/design-stages/initialize", { projectId: project.projectId, modelSource: A });
    // Creation was retired in #294. Retain the historical record through the
    // repository writer exactly as test_working_copies.py does, then cold-read it.
    // The open Runtime is the project's only writer (ADR-012): stop it first, write
    // outside it, and start it again before observations, which also leaves the
    // API's read caches nothing to be taught.
    const stopped = new Promise((resolve) => api.once("exit", resolve));
    api.kill("SIGTERM");
    await stopped;
    const groupSetup = spawnSync(python, ["-c", `
import json, sys
from pathlib import Path
from tools.dev import source_roots
source_roots.put_first(Path(sys.argv[2]))
from archflow.project.repository import FilesystemProjectRepository
from archflow.project.ports import PersistenceArea, PersistenceDestination
from archflow.project.record_kinds import STUDIO_WORKING_COPY
r = FilesystemProjectRepository.open(Path(sys.argv[1]))
a, b = json.loads(sys.argv[3]), json.loads(sys.argv[4])
r.put_json(run=r.load_run(a['runId']), destination=PersistenceDestination(PersistenceArea.RUN_RECORD, run_id=a['runId']), record_kind=STUDIO_WORKING_COPY,
 payload={'schema':'StudioWorkingCopy@1','projectId':'demo-project','groupId':'retained-options','label':'Retained options','stageId':'stage02','commonBase':a,
 'baseStageRef':sys.argv[5],'scope':['element:portico-base'],'options':[{'id':'A','label':'Original','modelSource':a},{'id':'B','label':'Higher','modelSource':b}],
 'selectedOptionId':'B','previousRevisionSha256':None})
`, projectRoot, repoRoot, JSON.stringify(A), JSON.stringify(B), stage.stageRef], { cwd: apiRoot, env, encoding: "utf8" });
    assert.equal(groupSetup.status, 0, groupSetup.stderr || groupSetup.stdout);
    await startRuntime();
    const group = await getJson("/api/working-copies/retained-options");
    const draft = await getJson("/api/working-draft");
    await request("PUT", "/api/working-draft", { projectId: project.projectId, baseRevisionSha256: draft.revisionSha256, runId: A.runId, branchId: "main" });
    const document = await request("POST", "/api/documents", { projectId: project.projectId, runId: A.runId,
      fileName: "retained-pages.pdf", mimeType: "application/pdf", contentBase64: (await readFile(path.join(root, "pages.pdf"))).toString("base64") });
    await request("PUT", "/api/document-annotations", { projectId: project.projectId, runId: document.runId,
      assetSha256: document.assetSha256, pageIndex: 0, baseRevisionSha256: null,
      annotations: [{ id: "retained-page-ink", kind: "freehand", points: [[0.1, 0.2], [0.25, 0.4], [0.4, 0.3]], color: "#2277dd", lineWidth: 0.004, label: null }],
      comment: "Keep this exact page and its saved ink" });
    const artifacts = await getJson("/api/artifacts");
    const nativeA = artifacts.artifacts.find((row) => row.runId === A.runId && row.representation !== "composed" && row.format === "3dm")?.modelSource;
    assert.ok(nativeA && nativeA.stateDigest === A.stateDigest && nativeA.assetSha256 !== A.assetSha256);
    return { root, projectRoot, project, group, sources: { A, B, nativeA, alternateB }, artifacts, document, webRoot, repoRoot,
      stage, apiOrigin, origin: apiOrigin, request, getJson, close };
  } catch (error) { await close(); throw error; }
}
