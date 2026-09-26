# GH-363

Issue: https://github.com/cogco1/MonkeyHub/issues/363
Base: `b73521c2`.

Projection phase 0a (ADR-008): stop re-deriving an unchanged project. Layout fingerprint and write serial; conditional reads with ETag/304 and a per-binding memo; artifact byte lookup through the memo and immutable content-addressed bytes; Hub binding check and idle watcher gated on the fingerprint; cheaper runtime snapshot.
