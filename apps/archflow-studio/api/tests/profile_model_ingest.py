"""Manual local benchmark; private source bytes live only in a disposable test project.

Run from apps/archflow-studio/api as python -m tests.profile_model_ingest --model PATH. This exercises the real API against
an empty test project; it measures external-source registration without inventing
a semantic design state. It does not measure browser/network/Board readiness. Output is anonymous
diagnostic JSON on stdout, not a project artifact or a committed model fixture.
"""
from __future__ import annotations

import argparse
import base64
import ctypes
import json
from pathlib import Path
import sys
import tempfile
from time import perf_counter

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings
from .support import PROJECT_ID, make_empty_project


def process_memory():
    """Windows working set; cumulative process peak is NOT a per-stage peak."""
    if sys.platform != "win32":
        return None

    class Counters(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("faults", ctypes.c_ulong)] + [
            (key, ctypes.c_size_t) for key in (
                "peak", "rss", "peak_paged", "paged", "peak_nonpaged", "nonpaged", "pagefile", "peak_pagefile",
            )
        ]

    value = Counters()
    value.cb = ctypes.sizeof(value)
    if not ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.c_void_p(-1), ctypes.byref(value), value.cb):
        return None
    return {"rss_bytes": value.rss, "process_peak_rss_bytes": value.peak}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    args = parser.parse_args()
    if args.model.suffix.lower() != ".3dm":
        parser.error("This benchmark exercises the existing 3dm API; SKP is not implemented here.")
    started = perf_counter()
    data = args.model.read_bytes()
    result = {"source_bytes": len(data), "file_read_ms": round((perf_counter() - started) * 1000, 2),
              "scope": "in_process_api_only", "samples": []}
    with tempfile.TemporaryDirectory(prefix="monkeyhub-ingest-benchmark-") as temporary:
        root = Path(temporary)
        repository = make_empty_project(root)
        app = create_app(StudioSettings(project_dir=root / PROJECT_ID,
                                       cad_export="off", monitor_dir=root / "monitor"))
        payload = {"projectId": PROJECT_ID,
                   "fileName": "benchmark.3dm", "contentBase64": base64.b64encode(data).decode()}
        observations = {}
        append = app.state.monitor.store.append

        def capture(event):
            observations[event.event_id] = (event, process_memory())
            append(event)

        app.state.monitor.store.append = capture
        with TestClient(app) as client:
            for mode in ("cold_registration", "warm_registration"):
                observations.clear()
                before = process_memory()
                started = perf_counter()
                response = client.post("/api/model-assets", json=payload)
                sample = {"mode": mode, "http_status": response.status_code,
                          "request_ms": round((perf_counter() - started) * 1000, 2),
                          "memory_before": before, "memory_after": process_memory(),
                          "stages": [{"phase": event.phase, "duration_ms": event.duration_ms,
                                      "status": event.status, "memory_at_end": memory}
                                     for event, memory in observations.values()
                                     if event.phase.startswith("model_ingest")]}
                result["samples"].append(sample)
                if response.status_code != 201:
                    print(json.dumps(result, indent=2))
                    return 1
                row = response.json()
                sample["object_count"] = row["objectCount"]
                started = perf_counter()
                download = client.get(f"/api/artifacts/{row['sha256']}/bytes", params={"runId": row["runId"]})
                sample.update(download_ms=round((perf_counter() - started) * 1000, 2),
                              download_status=download.status_code, exact_bytes_returned=download.content == data)
                if download.status_code != 200 or download.content != data:
                    print(json.dumps(result, indent=2))
                    return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
