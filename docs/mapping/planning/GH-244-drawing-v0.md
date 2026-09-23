# GH-244 — One model-linked cut plan

Status: active. Approved scope: [Drawing V0 audit](../../DRAWING_V0_AUDIT.md).

Extend the existing drawing/document/P036 owners to expose one horizontal cut
plan in MonkeyHub. Retain scale, lineweight, one hatch and semantic rectangular
opening dimensions. Moving dimensions changes only representation; driving an
eligible dimension uses the existing exact-base proposal/candidate path.

Freshness is derived against a named source or the source Stage's branch. Rebuild
preserves valid representation intent and old revisions; missing anchors are
explicit. Board review stays bound to its original page. Publish remains #66.

Local implementation and focused backend/browser checks are complete. The isolated room exercises real STEP section, semantic width candidate, old revision retention, explicit rebuild and broken anchors. Remaining acceptance: PR review of this first slice and the issue owner's architectural use review. Issue #244 stays open; Publish is #66. No user project or installed application changes.
