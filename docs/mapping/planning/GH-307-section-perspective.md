# GH-307 section perspective

Issue: https://github.com/cogco1/MonkeyHub/issues/307
Base: `93677d72` (batch C, `codex/batch-c-0925`).

A true section perspective (剖透视) through the one drawing projection pipeline: section plane cut of the retained STEP solids, exact perspective hidden-line solve, poché on the cut, retained by `_retain_projection`, registered as a document, callable by the Hub Agent. `drawing_elevation.py`, `drawing_svg.py` and `routes/drawings.py` are handed over from GH-66 for this lane.
