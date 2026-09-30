# ADR-003 — Two identities, never three

**Decision (2026-09-03):** `StateRecord.digest` is the content identity (design content without run_id and
base); `state_digest` is the binding identity (the developed projection bound to a run and base). Binding
a record to another run does not change its content identity.

**Do not:** add a third digest (an `authoredRecordDigest`, a scheme-versioned digest) to work around a
comparison; compare like with like.
