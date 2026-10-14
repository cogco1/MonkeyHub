You are reviewing a building model for relations between its elements that a designer relies on but did not write down. You receive the model's elements with their roles and bounding boxes, and the relations already declared. You cannot use tools or ask questions.

Propose the relations that are missing: ones that, if a later change broke them, an architect would want to be asked about. Answer with one JSON object and nothing else:

{"relations": [{"a": "...", "b": "...", "kind": "...", "reason": "..."}]}

- a, b: element ids exactly as written in the materials. For "support", a is the element below and b the element resting on it.
- kind, exactly one of: "support" (b rests on a's top), "abutment" (they touch side by side), "near" (they are within 5 cm without touching), "non_interference" (they must never overlap), "same_level" (their tops are at one level and should stay so).
- reason: one sentence.

Do not repeat a declared relation. Propose at most 20.
