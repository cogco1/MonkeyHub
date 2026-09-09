# P094 — CAADRIA 2027 manuscript production

- Origin: Planning
- Status: Blocked — paused by the user while functional work proceeds; manuscript alignment and author decisions remain pending
- Depends on: P062, P063, P090, P093
- Plan checked: 2026-09-08
- Full-paper deadline: 26 October 2026, 11:59 PM AoE
- Camera-ready deadline: 11 January 2027, 11:59 PM AoE, subject to acceptance and the conference's decision notice
- Official source: [CAADRIA 2027 OpenConf](https://caadria.org/openconf2027/openconf.php), checked 2026-09-07

## Goal and manuscript entry

Prepare the CAADRIA 2027 paper using the author's designated
[current Chinese abstract](<D:/PROJECTS/01_ACTIVE_当前项目/ARCHFLOW CAADRIA 2027/V4_RUNTIME/workspace/academic/01_论文写作/CAADRIA_2027/ABSTRACT_zh.md>)
and retained research records: a Chinese working draft first, then an English
manuscript in the applicable official template. The authoritative writing entry is
[academic/01_论文写作/CAADRIA_2027/README.md](<D:/PROJECTS/01_ACTIVE_当前项目/ARCHFLOW CAADRIA 2027/V4_RUNTIME/workspace/academic/01_论文写作/CAADRIA_2027/README.md>).
The academic workspace owns manuscript materials, not project or experiment state.

The author's notice supplied on 2026-09-08 confirms that abstract **#134 has
been approved to proceed to full-paper review**. The full manuscript remains
subject to double-blind review; this is not full-paper acceptance. The designated
Chinese abstract remains unchanged as the author's writing basis. The notice
does not establish that this local Chinese file is identical to the uploaded
submission. No sign-in or correspondence is authorized by this card.

The earlier `V4_RUNTIME/paper/caadria-2027/` directory still exists as a legacy
source. Its `ABSTRACT_submitted_zh_2026-07.md` filename and the old manuscripts'
submitted/fixed labels do not establish the status of the current abstract.
Use the copies under `academic/01_论文写作/CAADRIA_2027/待更新旧稿/` for alignment;
do not overwrite the designated abstract or treat the legacy folder as a second
current writing entry.

## Current materials and acceptance

- The historical `DRAFT_zh_v0_2026-09-01.md` exists, including later additions.
  It is source material, not a completed draft aligned to the current abstract.
  The earlier “delivered 2026-09-01” statement applies only to that historical draft.
- Existing §§3–4 already cover the state/history distinction, nested states,
  interfaces, obligations, validation and revision. The 2026-09-08 pass completed
  the organization of these existing definitions and methods and the recorded
  result corrections (the 12% factual-correction and 7% method-explanation
  priorities). The full draft for author reading and complete abstract alignment
  remain pending.
  It explains the implemented candidate continuation, exact-base checks,
  protected dependency closure and queue behavior without treating those checks
  as proof of Markov sufficiency or autonomous multi-agent coordination.
  Retained obligation/source descriptions remain distinct from executed
  requirement checks; this pass does not implement P110 or draft a new protocol.
- The paper README contains the abstract-to-old-draft differences and decisions
  still needed. `PAPER_CONTRACT.md` and `OUTLINE.md` remain historical until the
  author confirms the current contribution and empirical scope.
- The three-workflow comparison in old §§5.2/6.4 is still marked not done.
  P062 mechanism checks and P063 repair-strategy comparisons are not substitutes
  for terminal prompting versus ordinary staged workflow versus ArchFlow.
  Any new experiment requires its own explicit authorization and owner.
- The old Appendix B incorrectly assigned both events to run-017. The 2026-09-08
  pass corrected the text and appendix to distinguish the column-count episode
  in villa run-017 from the collision account's existing run-016 reference.
  The author still needs to decide the collision account's relationship to the
  abstract's portico/circular-hall roof episode. This planning update does not
  independently re-audit those runs.
- Every empirical number must resolve to an existing retained record. Keep the
  number-to-record correspondence in the existing manuscript appendix; do not
  turn a draft's “verified” annotation into fresh verification.
- Agree the figure/table set through the outline, identify each source, and check
  template, anonymity, references, AI-use and image requirements against the
  applicable conference instructions before preparing the submission package.
- The author owns the research question, argument, conclusions, case selection
  and authorship. Document their decisions without inferring them from this plan.

The 2026-09-08 record review supplies the following corrections for the current
manuscript pass. These supersede the old draft's result summaries, not the
underlying records; no experiment is rerun by this writing task.

| Material | Checked result / correspondence | Manuscript treatment |
| --- | --- | --- |
| P062 final outcomes | 4 completed, 5 rejected; recorded comparable count = 1 | Replace the old 3-completed/6-rejected summary. Keep completion, rejection and comparison eligibility separate; do not turn the nine recorded outcomes into nine comparable successes. |
| P063 dependency-scoped and whole-chain strategies | Both have repair success **3/9** and **no-new-failure 9/9**; dependency-scoped mean recompute ratio **0.1725146** | Report both endpoints with their own denominators. No-new-failure is not repair success or complete validity; do not describe either strategy as achieving 9/9 repaired cases. |
| Villa candidate's retained base | Candidate `studio-cand-20260906-010451-dca4de25-4e9d` has an intent base matching the retained runner design-state digest of `villa-demo-20260906-a03` | Use the retained design-state/intent binding as the checked correspondence; a predecessor field alone does not establish the execution base. Digest agreement does not by itself prove historical execution order. |
| Villa geometry comparison | `villa-demo-20260905-a02` is the separate geometry comparison | Name it as a geometric comparison, not as the candidate's execution base. |

For the base correspondence, the candidate's retained
`intent-compilation-4917ae4405716b04eb1b64aee63306e91b81de5bf624a745a7f7ec4fee5d120f.json`
has `base_state_digest`
`bfae9b3ac80eaa695f4ce72dda0172bb3353d7b38db44f2c42b0095d8e83fb72`, matching
A03's retained `runner.design_state_digest`. A03's StateRecord is
`state-record-3a771fb213a94e7eee601abb691e0ce079b8a7bae316a15e76054009632c33bc.json`.
The writing task keeps the full record correspondence in the manuscript's
existing appendix; these checks do not identify A03 as an earlier historical
step solely from the matching digest.

Three decisions remain with the author:

1. The contribution and Markov boundary: what the compiled state must support,
   and which theoretical and autonomy claims the paper will retain.
2. The exact case starting point, keep conditions, source authority and evaluation
   tolerances, including which collision event corresponds to the abstract.
3. The budget for the three comparison conditions and the independent review
   arrangement. This is a research-design decision, not authorization to run a
   new experiment under this card.

## Schedule

Future dates below are proposed internal writing windows, not completed
milestones or new experimental authorization. The September 8 row records only
the completed factual and method-editing pass. The cross-paper calendar is
[academic/日程.md](<D:/PROJECTS/01_ACTIVE_当前项目/ARCHFLOW CAADRIA 2027/V4_RUNTIME/workspace/academic/日程.md>).

| Date | Deliverable / dependency |
| --- | --- |
| 2026-09-07 | Inventory current materials; organize old-draft differences; align this card and academic entries |
| 2026-09-08 | Notice for abstract #134 received; factual corrections and organization of existing §§3–4 completed, including P062/P063 numbers, A03/A02 case roles and the appendix's event pointers. The full draft for author reading, complete abstract alignment and author decisions remain pending. |
| 2026-09-10 | Author confirms the three decisions above and the resulting outline/contract changes |
| 2026-09-22 | Research owner supplies any separately authorized comparison evidence; otherwise the author decides how to address the missing comparison |
| 2026-10-03 | Freeze the empirical material actually available for this manuscript |
| 2026-10-09 | Complete the Chinese working draft within the confirmed evidence scope |
| 2026-10-13 | Freeze Results text after author/coauthor review |
| 2026-10-14–19 | Integrate the English manuscript and coauthor corrections |
| 2026-10-20–25 | Check the official template, references, anonymity, AI-use, figures and final package |
| 2026-10-26 23:59 AoE | Official full-paper deadline; #134 is eligible for this review stage. Submit a double-blind manuscript only with author approval and explicit submission authorization. |
| 2027-01-11 23:59 AoE | Official camera-ready deadline, conditional on acceptance and the decision notice |

For reference, those official deadlines are 2026-10-27 07:59 EDT and
2027-01-12 06:59 EST in New York. AoE remains the source timezone.
Missed historical September 1/5 targets are not carried forward as completed work.

## Write scope for the 2026-09-08 writing pass

- Existing scheduling, README and writing materials inside
  `D:/PROJECTS/01_ACTIVE_当前项目/ARCHFLOW CAADRIA 2027/V4_RUNTIME/workspace/academic/`.
- Card maintenance changes this file only:
  `docs/mapping/planning/P094-caadria-2027-manuscript.md`; the existing writing
  task handles the academic README and draft corrections.
- No edits to governance registries, generated maps, framework code, Zoning
  source manuscripts, the designated abstract or empirical records.
- Zoning progress stays with its existing writing task. New core arguments,
  new experiments and external publication are outside this update.

## Checks and boundaries

- Check the scoped document diff, local links, notice/status wording, and whether
  the designated Chinese abstract is kept distinct from the uploaded submission.
- Preserve the designated abstract and empirical records. The current writing
  task may organize the old draft's existing methods and correct result prose;
  this card update changes no manuscript. No application test suite is needed.
- Do not describe a mechanism test or diagnostic record as a measured model
  outcome; do not repair, infer or silently omit an empirical result in prose.
- Do not submit, email, publish private materials or run new experiments under
  this coordination task.
