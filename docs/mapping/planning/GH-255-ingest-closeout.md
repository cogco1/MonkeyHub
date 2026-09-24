# GH-255 Rhino import closeout

Status: active.

Extend studio.artifacts, existing model upload API and retained model viewer.
Source: dfe0505d6adec2f49515aa3819935fc0fa1e6651 (YNNAP-HelloWorld).
Base: 72904b1bb6394f7df826f9f642a1e5fec0c6460f.

Retain immutable external bytes with exact registration identity; do not invent
architectural semantics, accept a Stage, or move design HEAD. Verify cold reopen,
unchanged-source reuse and distinct source updates before merge.
SKP and source-bound Board/AI Render/Publish remain outside this slice.
