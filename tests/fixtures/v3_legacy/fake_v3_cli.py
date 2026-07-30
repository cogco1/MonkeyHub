from __future__ import annotations

import json
import sys
import time


PROVIDER_ID = "provider.v3-fixture"
PROVIDER_VERSION = "fixture-1"
V3_FINGERPRINT = "f" * 64


def success(request: dict[str, object], *, provider_id: str = PROVIDER_ID) -> None:
    response = {
        "schema": "V3LegacyCapabilityOutput@1",
        "request_id": request["request_id"],
        "provider_id": provider_id,
        "provider_version": PROVIDER_VERSION,
        "capability_id": request["capability_id"],
        "v3_fingerprint": V3_FINGERPRINT,
        "workspace_id": request["workspace_id"],
        "base": request["base"],
        "kind": "observation",
        "evidence_refs": [
            *request["evidence_refs"],
            "v3://fixture/load-path-analysis",
        ],
        "payload": {
            "schema": "DetachedLoadPathObservation@1",
            "findings": [],
            "support_paths": [],
        },
    }
    print(json.dumps(response, sort_keys=True, separators=(",", ":")))


def main() -> int:
    mode = sys.argv[1]
    request = json.load(sys.stdin)
    if mode == "success":
        success(request)
        return 0
    if mode == "mismatch":
        success(request, provider_id="provider.other")
        return 0
    if mode == "malformed":
        print("not-json")
        return 0
    if mode == "oversized":
        print("x" * 2_000)
        return 0
    if mode == "timeout":
        time.sleep(2)
        return 0
    if mode == "exit":
        print("fixture failure", file=sys.stderr)
        return 7
    raise ValueError(f"unknown mode {mode}")


if __name__ == "__main__":
    raise SystemExit(main())
