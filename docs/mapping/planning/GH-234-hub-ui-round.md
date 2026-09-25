# GH-234 Hub UI round

Issue: https://github.com/cogco1/MonkeyHub/issues/234
Base: `04e719617d6aa20507b5c1846de3059b5c57a451`.

EXTEND existing owners only. Record the current UI reality and the functional contradictions it contains, propose the Hub-level interaction architecture for the expanded workspace set (Modeling, Board with Drawing, Render, Monitor, Fab, and Publish once it lands), and deliver bounded fixes for proven contradictions, each verified through the complete user action it repairs.

Files claimed by the open Publish integration (GH-66, PR #270) are not edited in this round; Drawing and Render findings in that area are reported to that work instead. The 2026-09-04 workbench research remains the MonkeyArch baseline this round extends.

Lane `view-base`: viewing a model never moves the editing base. Undo/Redo steps only through editing-base changes, and a refused or unsaved base switch is reported where the architect asked for it. Editing a viewed model that is not the editing base is refused until the architect continues from it (user decision Q1, 2026-09-24).

Lane `ui-audit`: `docs/2026-09-24-monkeyhub-ui-reality-audit.md` records the current UI reality and the functional contradictions, with evidence and verification status.

Lane `interaction-proposal`: a separate session writes the new Hub-level interaction proposal in `docs/2026-09-24-monkeyhub-interaction-proposal.md`, starting from the audit.
