# ADR-005 — A content-addressed record cannot carry its own reference

**Decision:** a record written through P036 is named by the digest of its bytes, so its own URI cannot be
inside it. The runner's in-memory receipt carries `receipt_ref` after the write; the retained file does
not, and readers take the reference from the record's own `ProjectRecordRef`.

**Do not:** add a self-reference field to a record and expect it on disk, or "fix" the runner by writing
twice.
