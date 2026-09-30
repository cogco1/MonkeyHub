# ADR-002 — Rhino is an executor, not the source of truth

**Decision:** the design state is the StateRecord; the compiled geometry program is derived from it; Rhino
(and, when P107 lands, OCCT) executes that program and its readback is evidence. `.3dm` is an
interoperability artifact.

**Why:** the record is content-addressed, diffable and replayable; a CAD document is none of those.

**Do not:** treat a `.3dm` as canonical, edit geometry in Rhino and read it back as design intent, or
reintroduce Rhino-side logic that decides what the design is.
