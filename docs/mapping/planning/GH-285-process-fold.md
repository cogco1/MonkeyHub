# GH-285 process fold

Issue: https://github.com/cogco1/MonkeyHub/issues/285 (the "过程折叠" part of section 6.1 in
[the interaction proposal](../../2026-09-24-monkeyhub-interaction-proposal.md)).
Base: `e183a69a`. Branch `codex/300-sidebar-process-fold`, shared with GH-300/sidebar.

Every tool call of an Agent turn was its own chat row (`studio_request · GET /api/board · completed`,
`rg -n … · failed`), and the rows buried the Agent's answer. Each turn now starts with one folded row,
"用时 1分18秒 · 12 步" / "Worked 1m 18s · 12 steps", that expands into plain-language steps; the raw
request lines stay under that row's "技术详情". Agent text, attachments and documents, candidate result
actions, permission prompts and errors stay visible and ungrouped. Opening a chat lands at its latest
message and a round jump-to-latest button returns there.

Not in this lane: result cards, the composer target label, the "新话题" rename and the "此项目" panel.
