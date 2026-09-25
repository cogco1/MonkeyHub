# GH-303 visual observation

Issue: https://github.com/cogco1/MonkeyHub/issues/303

Research-first V0 of one source-bound, read-only visual observation channel, with Modeling as its first consumer. The lane audits the current observation paths, adds a minimal `VisualReviewRequest`/`VisualObservation` contract, a pluggable provider over the existing Studio model transport and a Harness review budget in the `studio.intent` model seam, and exercises them with a dev benchmark tool. It compares deterministic readback with one and two visual reviews on a copy of a real project and checks one Board page through the same channel. Nothing is wired into production chat; findings and next slices go to `docs/2026-09-25-visual-observation.md`.
