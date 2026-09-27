# GH-364

Issue: https://github.com/cogco1/MonkeyHub/issues/364
Base: `38138370`.

Projection 0b (#364, ADR-008), frontend only: one request in flight per resource with If-None-Match (304 keeps the state), hidden surfaces are not mounted and mounted ones pause polling, rhino3dm initialises once per app with an in-memory model LRU by sha, and Modeling's opening requests run in parallel where they can.
