# GH-319

Issue: https://github.com/cogco1/MonkeyHub/issues/319
Base: `eed873ba` (cloud lanes, after batch I).

A model converted from GLB to 3DM displays correctly: the production conversion keeps or computes normals for the actual topology, keeps faces consistently wound, gives a neutral fallback colour to a mesh without material, keeps the source attributes it can, and says what it did.

Run as claude.ai cloud routines (2026-09-26); the root task integrates.
