# GH-365

Issue: https://github.com/cogco1/MonkeyHub/issues/365
Base: `d3e931c0`.

Projection phase 1a (ADR-008): the first read after a change stays fast. Verified record bytes and enumerations cached by content and directory stamps; state records parsed once per content digest; pure derivations memoized by the digests they read; per-run artifact and document listings memoized by run stamp. Same derivation code, byte-identical answers.
