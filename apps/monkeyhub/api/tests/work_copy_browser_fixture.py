"""Expose the existing disposable Hub/P036 fixture to the Board browser test."""

import base64
import json
import socket
import sys
import threading

import uvicorn

from test_project_runtime import ProjectRuntimeHttpTests, wait_for


def reply(payload):
    print(json.dumps(payload), flush=True)


def main():
    case = ProjectRuntimeHttpTests(methodName="runTest")
    try:
        case.setUp()
        with case.hub() as client:
            runtime_id = case.open_project(client)
            original_bytes = case.png_bytes("white")
            original = case.upload_image(client, runtime_id, original_bytes)
            _, work = case.open_work_copy(client, runtime_id, original)
            manager = client.app.state.runtimes
            runtime = manager.get(runtime_id)
            wait_for(lambda: runtime.work_copies and all(
                item.hashed_at_ns is not None for item in runtime.work_copies.values()
            ), "Initial work copy was not observed", timeout=20)
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", case.hub_port))
                server = uvicorn.Server(uvicorn.Config(client.app, lifespan="off", log_level="warning"))
                thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
                thread.start()
                try:
                    wait_for(lambda: server.started, "Isolated browser Hub did not listen")
                    reply({"ready": True, "origin": f"http://127.0.0.1:{listener.getsockname()[1]}",
                           "basePath": f"/api/runtime/projects/{runtime_id}/studio",
                           "runtimeId": runtime_id, "projectId": case.project_id,
                           "workPath": str(work), "original": original,
                           "originalBase64": base64.b64encode(original_bytes).decode("ascii"),
                           "editsBase64": [base64.b64encode(case.png_bytes(color)).decode("ascii")
                                           for color in ("red", "orange", "yellow", "blue", "green")]})
                    for line in sys.stdin:
                        command = json.loads(line)
                        if command == "stop":
                            break
                        if command != "snapshot":
                            raise ValueError(f"Unknown fixture command: {command}")
                        reply({"head": (case.project / "HEAD").read_text(encoding="utf-8"),
                               "artifactEvents": [row for row in manager.events.replay()
                                                  if row["kind"] == "artifact/updated"
                                                  and row["runtimeId"] == runtime_id]})
                finally:
                    server.should_exit = True
                    thread.join(timeout=10)
                    if thread.is_alive():
                        raise AssertionError("The isolated Hub socket did not stop")
    finally:
        case.doCleanups()


if __name__ == "__main__":
    main()
