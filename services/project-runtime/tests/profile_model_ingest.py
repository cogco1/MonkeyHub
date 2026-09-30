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
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import threading
from time import perf_counter
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from archflow_studio_api.main import create_app
from archflow_studio_api.settings import StudioSettings
from archflow.adapters import three_dm_inspector
from .support import PROJECT_ID, make_empty_project


def process_memory():
    """Return current process RSS without adding a benchmark dependency."""
    if sys.platform.startswith("linux"):
        try:
            resident_pages = int(Path("/proc/self/statm").read_text().split()[1])
            return resident_pages * os.sysconf("SC_PAGE_SIZE")
        except (OSError, ValueError, IndexError):
            return None
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
    return value.rss


@contextmanager
def measured_stage(name):
    """Sample stage RSS; the peak includes the already-running Python/API host."""
    samples = []
    stop = threading.Event()

    def sample():
        while not stop.wait(0.005):
            value = process_memory()
            if value is not None:
                samples.append(value)

    before = process_memory()
    if before is not None:
        samples.append(before)
    worker = threading.Thread(target=sample, daemon=True)
    worker.start()
    started = perf_counter()
    value = {"phase": name}
    try:
        yield value
    finally:
        value["duration_ms"] = round((perf_counter() - started) * 1000, 3)
        stop.set()
        worker.join()
        after = process_memory()
        if after is not None:
            samples.append(after)
        value["rss_before_bytes"] = before
        value["rss_after_bytes"] = after
        value["peak_rss_bytes"] = max(samples) if samples else None


def source_profile(path):
    stages = []
    with measured_stage("source_read") as read:
        data = path.read_bytes()
    stages.append(read)
    with measured_stage("source_digest") as digest:
        digest["sha256"] = hashlib.sha256(data).hexdigest()
    stages.append(digest)

    decoded = []
    original_decode = three_dm_inspector._decode_model

    def timed_decode(contents, rhino3dm):
        with measured_stage("rhino3dm_decode") as stage:
            model = original_decode(contents, rhino3dm)
        decoded.append(stage)
        return model

    with measured_stage("object_index_total") as index:
        with patch.object(three_dm_inspector, "_decode_model", side_effect=timed_decode):
            native_index = three_dm_inspector.inspect_three_dm_index(data)
    stages.extend(decoded)
    decode_ms = sum(row["duration_ms"] for row in decoded)
    index["excluding_decode_ms"] = round(max(0, index["duration_ms"] - decode_ms), 3)
    index["object_count"] = len(native_index["objects"])
    index["layer_count"] = len(native_index["layers"])
    stages.append(index)
    return data, stages


def distribution(values):
    return {"count": len(values), "min_ms": min(values), "median_ms": statistics.median(values),
            "max_ms": max(values)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--update-model", type=Path,
                        help="A distinct authorized/public 3dm revision; omitted means update is unmeasured.")
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    if args.model.suffix.lower() != ".3dm" or (args.update_model and args.update_model.suffix.lower() != ".3dm"):
        parser.error("This benchmark exercises the existing 3dm API; SKP is not implemented here.")
    if args.repeats < 2:
        parser.error("--repeats must be at least 2 so a distribution is reported.")
    data, source_stages = source_profile(args.model)
    update_data, update_source_stages = source_profile(args.update_model) if args.update_model else (None, None)
    if update_data is not None and hashlib.sha256(update_data).digest() == hashlib.sha256(data).digest():
        parser.error("--update-model must have a different exact source digest.")
    result = {
        "environment": {"platform": sys.platform, "python": sys.version.split()[0],
                        "rss_sampling_interval_ms": 5, "repeats": args.repeats},
        "input": {"source_bytes": len(data), "source_sha256": hashlib.sha256(data).hexdigest(),
                  "update_bytes": len(update_data) if update_data else None,
                  "fixture_caveat": "Public or synthetic fixtures are not representative real architectural models."},
        "definitions": {"cold": "new empty P036 project; process and OS caches are not reset",
                        "warm": "unchanged exact digest in the same project",
                        "update": "different exact digest in the same project",
                        "view_consumption": "exact retained-byte API download only",
                        "browser_first_frame": "not measured", "skp": "not measured"},
        "scope": "in_process_api_and_native_index; no network, browser decode, GPU upload, Board, or Render",
        "source_stages": source_stages, "update_source_stages": update_source_stages, "samples": [],
    }
    for repetition in range(args.repeats):
        with tempfile.TemporaryDirectory(prefix="monkeyhub-ingest-benchmark-") as temporary:
            root = Path(temporary)
            make_empty_project(root)
            app = create_app(StudioSettings(project_dir=root / PROJECT_ID,
                                            cad_export="off", monitor_dir=root / "monitor"))
            observations = {}
            append = app.state.monitor.store.append

            def capture(event):
                observations[event.event_id] = (event, process_memory())
                append(event)

            app.state.monitor.store.append = capture
            with TestClient(app) as client:
                modes = [("cold_registration", data), ("warm_unchanged", data)]
                if update_data is not None:
                    modes.append(("changed_source", update_data))
                for mode, source_data in modes:
                    observations.clear()
                    payload = {"projectId": PROJECT_ID, "fileName": "benchmark.3dm",
                               "contentBase64": base64.b64encode(source_data).decode()}
                    with measured_stage("registration_request") as request_stage:
                        response = client.post("/api/model-assets", json=payload)
                    sample = {"repetition": repetition + 1, "mode": mode, "http_status": response.status_code,
                              "request": request_stage,
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
                    with measured_stage("retained_bytes_view_consumption") as view_stage:
                        download = client.get(f"/api/artifacts/{row['sha256']}/bytes",
                                              params={"runId": row["runId"]})
                    sample.update(view_consumption=view_stage, download_status=download.status_code,
                                  exact_bytes_returned=download.content == source_data,
                                  registered_sha256=row["sha256"])
                    if download.status_code != 200 or download.content != source_data:
                        print(json.dumps(result, indent=2))
                        return 1
    modes = sorted({sample["mode"] for sample in result["samples"]})
    result["distributions"] = {}
    for mode in modes:
        samples = [sample for sample in result["samples"] if sample["mode"] == mode]
        phase_names = sorted({stage["phase"] for sample in samples for stage in sample["stages"]})
        peaks = [sample["request"]["peak_rss_bytes"] for sample in samples
                 if sample["request"]["peak_rss_bytes"] is not None]
        result["distributions"][mode] = {
            "registration_request": distribution([sample["request"]["duration_ms"] for sample in samples]),
            "registration_peak_rss_bytes": max(peaks) if peaks else None,
            "retained_bytes_view_consumption": distribution([
                sample["view_consumption"]["duration_ms"] for sample in samples]),
            "stages": {phase: distribution([
                stage["duration_ms"] for sample in samples for stage in sample["stages"] if stage["phase"] == phase
            ]) for phase in phase_names},
        }
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
