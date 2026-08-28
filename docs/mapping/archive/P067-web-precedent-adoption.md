# P067 — Web precedent retrieval and adoption

- Origin: Planning
- Status: Ready
- Depends on: P028, P060, P062

## Goal

Retrieve building precedent evidence directly from the live web and carry
it into generation as authority-gated typed facts, closing the knowledge
gap P066 exposed (a portico authored flat because no typology canon
reached the generation context).

## Trust boundary (the point of the card)

Naive RAG pastes retrieved text into the prompt. Here the web is
untrusted input three gates away from generation:

1. **Snapshot** — a page is retained as a content-addressed record (URL,
   retrieval time, raw and text digests). Snapshots carry no authority
   and are never fed raw into a provider prompt.
2. **Quoted facts** — candidate facts cite exact quotes with character
   spans inside a named snapshot; harness extraction is explicitly marked
   and open to human review.
3. **Adoption** — only a typed adoption record under a named authority
   promotes facts. Each adopted fact compiles into a build-policy
   constraint whose provenance chains constraint → adoption → snapshot →
   URL, and the provider must answer it through the existing
   required-response validation (M057). Ignoring an adopted fact is a
   typed rejection, not silence.

## Measurement

A paired live observation: the retained P066 attempt without precedent
context beside one attempt with adopted canon constraints — the canon
analogue of the P062 relationship ablation (which measured 1.0 → 0.0
relationship coverage when context was withheld).

## Stop conditions

- Stop if raw page text would enter a prompt or acquire authority.
- Stop if a fact cannot cite an exact retained quote.
- Stop before claiming model capability from one paired observation.

## Tests

- Snapshot digests, quote-span binding, adoption authority, and
  no-raw-text-in-prompt guarantees.
- Constraint compilation provenance chains.
- Architecture V3 scope diff, compileall, full discovery.


## Completion

- Completed: 2026-08-28
- Evidence: live paired observation retained: without precedent the model authored a flat portico slab; with three web-quoted adopted facts it answered every constraint, added a pediment component, and authored a genuine triangular-extrusion pitched pediment plus a revolve dome; snapshot/quote-span/adoption/provenance chain proven by 5 contract tests; raw page text never entered a prompt; ARCHITECTURE PASS
