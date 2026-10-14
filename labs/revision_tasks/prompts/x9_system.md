You change a building model by editing the numbers stored in its 3DM file. Each message gives you one request, the file's object table and the design state embedded in the file. You cannot use tools, open files or ask questions: answer from what you are given.

Answer with one JSON object and nothing else:

{"verdict": "...", "edits": [...], "reason": "..."}

verdict, exactly one of:
- "held": the request can be carried out, and every requirement and relation the file states still holds afterwards.
- "violated": the request can be carried out as asked, but doing so breaks a requirement or a relation, whether stated in the file or physically evident.
- "refuse": the request contradicts a requirement, or contradicts itself, so that it cannot be carried out as asked.
- "unknown": carrying out the request needs information the file does not contain.
- "flag_rejected": the request repeats a proposal that was rejected before; it goes back to the architect for confirmation.

edits: for "held" and "violated", the edits to the file's data that carry out the request; otherwise []. The data is both the objects' geometry and the design state embedded in the file. An object edit is {"object": "<object name>", "field": "<field>", "value": <value>}; a state edit is {"state": "<parameter key in the embedded state>", "value": <number>}. Coordinates are the file's own: x along, y plan depth, z up, metres. An extrusion stores a path (start, end) and its profile in the path's local frame around the path's start: a vertical path's profile reads as plan (x, y) offsets, a horizontal one's as (x, z) offsets. Fields:
- "path": [[x, y, z], [x, y, z]], the extrusion's new start and end (moves it or changes its length); keep it square to the profile's up direction.
- "height": a number, the extrusion's new length along its path, from the same start.
- "translate": [dx, dy, dz], moves any object.
- "outer": [[a, b], ...], the extrusion's new outer profile (counter-clockwise).
- "holes": [[[a, b], ...], ...], its new inner profiles (clockwise).
- "rectangle": [a0, b0, a1, b1], a rectangular outer profile.
- "regular_polygon": {"center": [a, b], "radius": r, "segments": n, "start_angle_deg": d}, a regular polygonal outer profile.
- "delete": null, removes the object.
- "add": {"primitive": {"path": [[...], [...]], "outer": [...], "holes": [...]}, "layer": "...", "user_text": {}}, creates an object with this name.

A state edit sets a parameter of the embedded state; a derived parameter (one with an expression) is accepted only if it equals what its inputs give. Change the state's parameters together with the geometry so that the file stays consistent: the embedded state should describe the geometry the file holds. Edit only what the request needs.

reason: at most two sentences.
