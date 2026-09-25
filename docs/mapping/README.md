# Work ledgers

`docs/mapping/planning/` now contains only the **remaining legacy R/M/P work cards** that still matter to current development. The historical RMP namespace is frozen: existing identities remain valid, but new work does not allocate `P116+` or new M/R numbers.

New requirements start as GitHub Issues; Pull Requests are their implementation/review units. `governance/work_registry.json` is a live source-scope and concurrency registry, not a second backlog. Existing legacy cards may remain until their acceptance is complete, while finished implementation history remains in Git history.

The transition is intentionally non-destructive: historical commits and retained references keep their old P/M/R ids, and P115, whose lanes have all closed, is kept as a frozen historical index rather than renumbered.