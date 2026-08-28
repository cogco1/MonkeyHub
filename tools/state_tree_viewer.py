"""Read-only live design-state tree viewer over P036 project directories.

This is a derived, disposable development view in the spirit of P057: it scans
project records, renders a browser tree bound to the exact backing files, and
owns no design, validation, persistence, or promotion authority. It never
writes inside the served root.

Usage:
    python tools/state_tree_viewer.py <root> [--port 8766]

``<root>`` is either one P036 project directory (contains ``canonical/``) or a
directory of projects (e.g. ``probes/``). The page polls a cheap directory
fingerprint and re-renders only when files actually change.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

_STAGE_RE = re.compile(r"^(?P<kind>.+?)-(?P<stage>\d{3})-[0-9a-f]{64}\.json$")
_DIGEST_RE = re.compile(r"-(?P<digest>[0-9a-f]{64})\.json$")
_CANONICAL_RE = re.compile(r"^state-v(?P<version>\d+)-[0-9a-f]{64}\.json$")


def _short(digest: str | None) -> str:
    return (digest or "")[:12]


class _RecordCache:
    """mtime-keyed JSON parse cache so polling stays cheap."""

    def __init__(self) -> None:
        self._entries: dict[str, tuple[int, dict]] = {}
        self._lock = threading.Lock()

    def load(self, path: Path) -> dict:
        try:
            stat = path.stat()
        except OSError as exc:
            return {"error": str(exc)}
        key = str(path)
        with self._lock:
            hit = self._entries.get(key)
            if hit and hit[0] == stat.st_mtime_ns:
                return hit[1]
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(parsed, dict):
                parsed = {"error": "record is not a JSON object"}
        except (OSError, ValueError) as exc:
            parsed = {"error": f"unreadable record: {exc}"}
        with self._lock:
            self._entries[key] = (stat.st_mtime_ns, parsed)
        return parsed


_CACHE = _RecordCache()


def fingerprint(root: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(root.rglob("*.json")):
        try:
            stat = path.stat()
        except OSError:
            continue
        h.update(str(path.relative_to(root)).encode("utf-8", "replace"))
        h.update(f":{stat.st_mtime_ns}:{stat.st_size};".encode())
    return h.hexdigest()[:16]


def _node(label: str, kind: str, *, path: Path | None = None,
          root: Path | None = None, badge: str = "",
          meta: dict | None = None, children: list | None = None,
          flag: str = "") -> dict:
    rel = str(path.relative_to(root)) if path is not None and root else ""
    digest = ""
    if path is not None:
        m = _DIGEST_RE.search(path.name)
        digest = _short(m.group("digest")) if m else ""
    return {
        "label": label, "kind": kind, "path": rel, "digest": digest,
        "badge": badge, "flag": flag, "meta": meta or {},
        "children": children or [],
    }


def _component_tree(record: dict, prev_revisions: dict[str, int],
                    path: Path, root: Path) -> list[dict]:
    content = record.get("content") or record
    components = content.get("components") or []
    by_parent: dict[str | None, list[dict]] = {}
    for comp in components:
        if isinstance(comp, dict):
            by_parent.setdefault(comp.get("parent_component_id"), []).append(comp)

    def build(parent: str | None) -> list[dict]:
        nodes = []
        for comp in by_parent.get(parent, []):
            cid = str(comp.get("component_id"))
            revision = int(comp.get("revision") or 0)
            previous = prev_revisions.get(cid)
            flag = "changed" if previous is not None and previous != revision \
                else ("new" if previous is None and prev_revisions else "")
            nodes.append(_node(
                cid, "component", path=path, root=root,
                badge=f"{comp.get('maturity', '?')} r{revision}", flag=flag,
                meta={
                    "semantic_kind": comp.get("semantic_kind"),
                    "intent": comp.get("intent"),
                    "revision": revision,
                    "volume_ids": comp.get("volume_ids"),
                },
                children=build(cid),
            ))
        return nodes

    return build(None)


def _revisions(record: dict) -> dict[str, int]:
    content = record.get("content") or record
    out: dict[str, int] = {}
    for comp in content.get("components") or []:
        if isinstance(comp, dict) and comp.get("component_id") is not None:
            out[str(comp["component_id"])] = int(comp.get("revision") or 0)
    return out


def _summarise(record: dict) -> dict:
    content = record.get("content") if isinstance(record.get("content"), dict) else {}
    meta = {
        "schema": record.get("schema"),
        "content_schema": content.get("schema"),
        "role": record.get("role"),
        "base_version": (record.get("base") or {}).get("version"),
    }
    if "error" in record:
        meta["error"] = record["error"]
    for key in ("active_phase", "coordination_status", "status", "lifecycle",
                "option_id", "branch_id", "hard_usability_verdict",
                "selected", "label"):
        if key in content and content[key] is not None:
            meta[key] = content[key]
    obligations = content.get("obligations")
    if isinstance(obligations, list) and obligations:
        open_count = sum(1 for o in obligations
                         if isinstance(o, dict) and o.get("status") == "open")
        meta["obligations"] = f"{open_count} open / {len(obligations)} total"
    return {k: v for k, v in meta.items() if v is not None}


_ROLE_ORDER = ("provider-invocation", "component-proposal", "design-state",
               "geometry-program", "lifecycle-receipt")


def _run_tree(run_dir: Path, root: Path) -> dict:
    records_dir = run_dir / "records"
    staged: dict[str, list[tuple[int, int, Path, dict]]] = {}
    plain: dict[str, list[tuple[Path, dict]]] = {}
    for path in sorted(records_dir.glob("*.json")):
        record = _CACHE.load(path)
        match = _STAGE_RE.match(path.name)
        if match:
            try:
                mtime = path.stat().st_mtime_ns
            except OSError:
                mtime = 0
            staged.setdefault(match.group("kind"), []).append(
                (int(match.group("stage")), mtime, path, record))
        else:
            kind = _DIGEST_RE.sub("", path.name)
            plain.setdefault(kind, []).append((path, record))

    # The filename index alone does not order same-index successors, so
    # iterations are reconstructed per role by (index, write time).
    for entries in staged.values():
        entries.sort(key=lambda e: (e[0], e[1]))

    def role_rank(kind: str) -> int:
        for rank, role in enumerate(_ROLE_ORDER):
            if role in kind:
                return rank
        return len(_ROLE_ORDER)

    stage_nodes, prev_revisions = [], {}
    component_root: list[dict] = []
    depth = max((len(v) for v in staged.values()), default=0)
    for index in range(depth):
        children = []
        for kind in sorted(staged, key=role_rank):
            if index >= len(staged[kind]):
                continue
            _, _, path, record = staged[kind][index]
            children.append(_node(kind, "record", path=path, root=root,
                                  meta=_summarise(record)))
            if "component-proposal" in kind and "error" not in record:
                component_root = _component_tree(record, prev_revisions,
                                                 path, root)
                prev_revisions = _revisions(record)
        stage_nodes.append(_node(f"iteration {index} (write order)", "stage",
                                 children=children,
                                 badge=f"{len(children)} records"))

    other_nodes = [
        _node(kind, "record-group" if len(entries) > 1 else "record",
              path=entries[-1][0], root=root,
              badge=f"x{len(entries)}" if len(entries) > 1 else "",
              meta=_summarise(entries[-1][1]),
              children=[
                  _node(p.name[:44], "record", path=p, root=root,
                        meta=_summarise(r))
                  for p, r in entries[:-1]
              ] if len(entries) > 1 else [])
        for kind, entries in sorted(plain.items())
    ]

    children = []
    if component_root:
        children.append(_node("component tree (latest stage)", "section",
                              children=component_root))
    if stage_nodes:
        children.append(_node("production stages (D_v,k)", "section",
                              children=stage_nodes))
    if other_nodes:
        children.append(_node("records", "section", children=other_nodes))
    for extra in ("branches", "candidates", "reviews", "workspaces",
                  "recovery"):
        sub = run_dir / extra
        if sub.is_dir():
            entries = sorted(p for p in sub.rglob("*") if p.is_file())
            if entries:
                children.append(_node(
                    extra, "dir", badge=f"{len(entries)} files",
                    children=[_node(p.name[:44], "file", path=p, root=root)
                              for p in entries[:60]]))
    return _node(run_dir.name, "run", children=children)


def project_tree(project_dir: Path, root: Path) -> dict:
    children = []
    canonical = []
    for path in sorted((project_dir / "canonical").glob("state-v*.json")):
        match = _CANONICAL_RE.match(path.name)
        if not match:
            continue
        record = _CACHE.load(path)
        state = record.get("state") or {}
        canonical.append(_node(
            f"C_v{int(match.group('version'))}", "canonical",
            path=path, root=root, badge=str(state.get("phase") or ""),
            meta={
                "schema": record.get("schema"),
                "parent": record.get("parent"),
                "state_sha256": _short(record.get("state_sha256")),
                "authoritative_records":
                    len(state.get("authoritative_record_refs") or []),
            }))
    if canonical:
        children.append(_node("canonical chain (C_v)", "section",
                              children=canonical))

    runs_dir = project_dir / "runs"
    if runs_dir.is_dir():
        run_nodes = [_run_tree(d, root) for d in sorted(runs_dir.iterdir())
                     if d.is_dir()]
        if run_nodes:
            children.append(_node("runs", "section", children=run_nodes))

    counts = []
    for name in ("events", "objects", "input", "exports"):
        sub = project_dir / name
        if sub.is_dir():
            total = sum(1 for p in sub.rglob("*") if p.is_file())
            counts.append(f"{name}: {total}")
    return _node(project_dir.name, "project", badge=" · ".join(counts),
                 children=children)


def discover_projects(root: Path) -> list[Path]:
    if (root / "canonical").is_dir():
        return [root]
    return sorted(d for d in root.iterdir()
                  if d.is_dir() and (d / "canonical").is_dir())


_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>ArchFlow design-state tree</title><style>
:root { --bg:#14161a; --panel:#1c1f26; --text:#d7dae0; --dim:#7d8590;
  --accent:#6cb6ff; --line:#2b303b; --changed:#e3b341; --new:#57ab5a; }
* { box-sizing:border-box; margin:0; }
body { background:var(--bg); color:var(--text); display:flex; height:100vh;
  font:13px/1.5 ui-monospace,Consolas,monospace; }
#tree { flex:1; overflow:auto; padding:14px 18px; }
#side { width:340px; border-left:1px solid var(--line); padding:14px;
  background:var(--panel); overflow:auto; flex-shrink:0; }
h1 { font-size:14px; margin-bottom:2px; }
#status { color:var(--dim); font-size:11px; margin-bottom:10px; }
#status.live::before { content:"● "; color:var(--new); }
select { background:var(--panel); color:var(--text); border:1px solid
  var(--line); border-radius:4px; padding:2px 6px; margin-bottom:10px; }
details { padding-left:16px; }
#tree > details { padding-left:0; }
summary { cursor:pointer; white-space:nowrap; border-radius:4px;
  padding:0 4px; list-style-position:outside; }
summary:hover { background:var(--line); }
summary.sel { background:#28405c; }
.badge { color:var(--dim); font-size:11px; margin-left:8px; }
.digest { color:#4d5566; font-size:10px; margin-left:8px; }
.k-project > summary { color:#e6edf3; font-weight:600; }
.k-section > summary { color:var(--accent); }
.k-canonical > summary { color:#c297ff; }
.k-component > summary { color:#7ee787; }
.k-stage > summary { color:#ffa657; }
.f-changed > summary { outline:1px solid var(--changed); }
.f-changed > summary::after { content:" changed"; color:var(--changed);
  font-size:10px; }
.f-new > summary::after { content:" new"; color:var(--new); font-size:10px; }
#side h2 { font-size:12px; color:var(--accent); margin-bottom:8px;
  word-break:break-all; }
#side .row { margin-bottom:6px; word-break:break-all; }
#side .key { color:var(--dim); font-size:11px; }
.leaf { list-style:none; }
.leaf::-webkit-details-marker { display:none; }
@keyframes flash { from { background:#233043; } to { background:none; } }
.updated { animation:flash 0.9s; }
</style></head><body>
<div id="tree"><h1>ArchFlow design-state tree</h1>
<div id="status">connecting…</div>
<select id="proj"></select><div id="nodes"></div></div>
<div id="side"><h2>details</h2>
<div id="detail" class="row key">click a node</div></div>
<script>
let fp = "", project = "", timer = null;
const openSet = new Set(), $ = id => document.getElementById(id);

function render(node, path) {
  const id = path + "/" + node.label;
  const d = document.createElement("details");
  d.className = "k-" + node.kind + (node.flag ? " f-" + node.flag : "");
  if (!node.children.length) d.classList.add("leafwrap");
  if (openSet.has(id) || node.kind === "project" || node.kind === "section")
    d.open = true;
  d.addEventListener("toggle", () =>
    d.open ? openSet.add(id) : openSet.delete(id));
  const s = document.createElement("summary");
  if (!node.children.length) s.className = "leaf";
  s.textContent = node.label;
  if (node.badge) {
    const b = document.createElement("span");
    b.className = "badge"; b.textContent = node.badge; s.appendChild(b);
  }
  if (node.digest) {
    const g = document.createElement("span");
    g.className = "digest"; g.textContent = node.digest; s.appendChild(g);
  }
  s.addEventListener("click", ev => {
    document.querySelectorAll("summary.sel")
      .forEach(e => e.classList.remove("sel"));
    s.classList.add("sel");
    showDetail(node);
    if (!node.children.length) ev.preventDefault();
  });
  d.appendChild(s);
  node.children.forEach(c => d.appendChild(render(c, id)));
  return d;
}

function showDetail(node) {
  const rows = [["kind", node.kind]];
  if (node.path) rows.push(["file", node.path]);
  if (node.digest) rows.push(["digest", node.digest + "…"]);
  Object.entries(node.meta).forEach(([k, v]) =>
    rows.push([k, typeof v === "object" ? JSON.stringify(v) : String(v)]));
  $("detail").innerHTML = rows.map(([k, v]) =>
    `<div class="row"><div class="key">${k}</div><div>${
      String(v).replace(/</g, "&lt;")}</div></div>`).join("");
}

async function poll() {
  try {
    const r = await fetch(`/api/tree?project=${encodeURIComponent(project)}&fp=${fp}`);
    const data = await r.json();
    $("status").className = "live";
    if (data.unchanged) {
      $("status").textContent =
        "live · unchanged · " + new Date().toLocaleTimeString();
    } else {
      fp = data.fp;
      const box = $("nodes");
      box.innerHTML = "";
      box.appendChild(render(data.tree, ""));
      box.classList.remove("updated"); void box.offsetWidth;
      box.classList.add("updated");
      $("status").textContent =
        "live · updated " + new Date().toLocaleTimeString() + " · fp " + fp;
    }
  } catch (e) {
    $("status").className = "";
    $("status").textContent = "server unreachable — retrying";
  }
  timer = setTimeout(poll, 1500);
}

async function init() {
  const projects = await (await fetch("/api/projects")).json();
  const sel = $("proj");
  projects.forEach(p => {
    const o = document.createElement("option");
    o.value = o.textContent = p; sel.appendChild(o);
  });
  project = projects[0] || "";
  sel.addEventListener("change", () => {
    project = sel.value; fp = ""; openSet.clear();
    clearTimeout(timer); poll();
  });
  poll();
}
init();
</script></body></html>"""


def make_handler(root: Path):
    projects = {p.name: p for p in discover_projects(root)}

    class Handler(BaseHTTPRequestHandler):
        def _send(self, body: bytes, content_type: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 (http.server API)
            url = urlparse(self.path)
            if url.path == "/":
                self._send(_PAGE.encode("utf-8"), "text/html")
                return
            if url.path == "/api/projects":
                nonlocal projects
                projects = {p.name: p for p in discover_projects(root)}
                self._send(json.dumps(sorted(projects)).encode(),
                           "application/json")
                return
            if url.path == "/api/tree":
                query = parse_qs(url.query)
                name = (query.get("project") or [""])[0]
                target = projects.get(name)
                if target is None:
                    self._send(json.dumps({"error": "unknown project"}).encode(),
                               "application/json")
                    return
                fp = fingerprint(target)
                if fp == (query.get("fp") or [""])[0]:
                    payload = {"unchanged": True, "fp": fp}
                else:
                    payload = {"fp": fp, "tree": project_tree(target, root)}
                self._send(json.dumps(payload).encode(), "application/json")
                return
            self.send_error(404)

        def log_message(self, *args) -> None:
            pass

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    root = args.root.resolve()
    if not root.is_dir():
        raise SystemExit(f"not a directory: {root}")
    if not discover_projects(root):
        raise SystemExit(f"no P036 project (canonical/) found under: {root}")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(root))
    print(f"serving read-only state tree for {root} "
          f"on http://127.0.0.1:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
