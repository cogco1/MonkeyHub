# GH-252

Issue: https://github.com/cogco1/MonkeyHub/issues/252
Base: `a512018a` (batch F).

Monitor prices usage: Studio and Hub producers write details.billing_plan and rates.json carries the plan rows, so usage matches a price.

Batch G (2026-09-25), planned in the owner's order; plans are kept outside the repo.

## Landed in the billing-plan lane

- **Why nothing matched.** `match_rate` answers `missing_identity` for an event without `details.billing_plan`, and no producer wrote one. No `rates.json` row had a plan either, so even a planned event would have found no row.
- **One vocabulary.** `details.billing_plan` takes only `usage.BILLING_PLANS`:
  - `api-standard`: a provider's public standard synchronous tier.
  - `coding-plan`: a flat third-party plan with no per-token price.
- **One derivation.** `pricing.billing_plan(provider, usage)` maps the connection a producer records to its plan:
  - `anthropic`, `claude`, `codex` and `openai` map to `api-standard`, and `coding-plan` maps to `coding-plan`.
  - Usage metadata that reports fast mode, priority, batch or US-only inference withholds the standard plan.
  - Studio's `StudioMonitor.record`, the Hub's `HubTurnObserver.claude_usage` and the Codex session reader all call it.
- **Catalog.** The OpenAI rows carry `api-standard`. New Claude rows cover the Anthropic API and Claude CLI connections: Fable 5.1, Opus 5.5, Opus 5, Sonnet 5 and Haiku 4.5, from the published price list as read on 2026-09-25.
- **What prices now.**
  - Studio Anthropic calls and Hub Claude CLI calls on those models are `matched`, and their turns carry a price.
  - Codex session usage is `ambiguous`. The OpenAI rows split short and long context, and one event cannot say which it was.
  - Studio `codex exec` is `not_found`: it has no row of its own, and OpenAI's rows are not its alias.
  - Coding Plan is `not_found`.
  - Gemini renders carry no plan (`missing_identity`), because their output mixes text and image tokens.

## Open

- **Codex context tier.** Codex usage can be priced exactly only once a row states the input bound of its context tier and the event is known to be one request. `last_token_usage` and the session's context window could establish both. That change belongs to `monkeymonitor/codex.py`, `pricing.py` and `rates.json`.
- **Usage page.** Each trace carries `price` with per-event match statuses, but the Usage page shows only the manual calculator. Showing a turn's price and why it is unknown is a `MonitorPage.tsx` change (hub.shell).
- **Plan setting.** The plan is derived from the provider setting alone. Whether a Claude CLI or Codex login is an API key or a subscription is not a setting, so Hub Claude usage keeps `billing_mode` unknown. A per-connection "API key / subscription" choice would need `UserSettingsDto` and the settings page. It would set `billing_mode`, not the plan.
- **Studio receipt tiers.** The Studio Anthropic receipt does not carry the reported `service_tier`, `speed` or `inference_geo` (`intent_agent.py`, studio.intent). Studio requests none of them, but a workspace whose default inference geography is US-only would still be priced at standard rates.
- **Model id spellings.** Ids are never aliased. A CLI that reports a suffixed id, such as `claude-opus-5[1m]` seen in pilot probes, matches no row.
