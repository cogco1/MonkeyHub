# GH-376

Issue: https://github.com/cogco1/MonkeyHub/issues/376
Base: `c4ed9500`.

Projection acceptance without the owner's machine (ADR-008): a deterministic synthetic P036 project, tools/projection_check.py comparing a base and a candidate code root byte for byte before and after one review write (plus conditional reads and immutable bytes, relative timings), and a verify.yml projection job on ubuntu-latest and windows-latest.
