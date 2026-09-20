# Checkpoint critique: research basis and limits

This experiment asks whether feedback timing and a separate reviewer context
improve a bounded design decision task under a shared total budget. It does not
assume that adding an agent improves results. The literature motivates the
comparison; it does not establish an outcome for this fixture, provider, or model.

The sources below were inspected on 2026-09-20 through the original papers and
author repositories. Repository commits are inspected snapshots, not claims that
the authors ran their published experiments at those exact commits. The cited
implementations are research code, not production components to import wholesale.

## What the four arms isolate

All arms use the same synthetic courtyard fixture, public policy, finite allowed
operations, stage rules, and independent final predicates. The implementation is
in [fixture.py](fixture.py), [harness.py](harness.py), and [provider.py](provider.py).

| Arm | Reviewer context | Timing |
| --- | --- | --- |
| A: single agent, after | Public proposer transcript retained for self-review | After all three decision stages |
| B: single agent, during | Same public transcript rule | At the three fixed decision checkpoints |
| C: Proposer + Critic, after | Fresh Critic context with the same candidate, policy, and exact binding | After all three decision stages |
| D: Proposer + Critic, during | Same fresh Critic context rule | At the same three fixed decision checkpoints |

The fixed Claude CLI configuration has no model tools or MCP servers. Each CLI
invocation starts fresh; self-review continuity is supplied by the explicit public
transcript, not a hidden native session. The separate Critic uses the same model
configuration as the proposer. It is a context and role intervention, **not an
independent model, an independent source of truth, or a second final evaluator**.
The provider configuration and observed model identity must accompany live results.

Immediate typed and protected invariants are enforced in every arm. The reviewer
may request declared goal checks; the harness returns their deterministic evidence
and allows one bounded repair per review. Permitted unfinished work remains valid
at its declared stage. Final assessment is computed independently and does not
feed a repair prompt. Requested checks and final assessment can use the same fixed
predicates; independence here means that the final decision does not depend on a
model's self-rating. It does not mean that a second hidden specification exists.

## Original work and inspected implementations

| Work and paper version | Inspected repository commit and licensing | Actual mechanism and useful code |
| --- | --- | --- |
| **Self-Refine**, Madaan et al., NeurIPS 2023. [arXiv v2](https://arxiv.org/html/2303.17651v2) | `madaan/self-refine@9a206d41e5d2d0c241bb441f41eeadb945afaa55`; [Apache-2.0](https://github.com/madaan/self-refine/blob/9a206d41e5d2d0c241bb441f41eeadb945afaa55/LICENSE) | One model generates an output, gives feedback, and revises. [src/gsm/run.py](https://github.com/madaan/self-refine/blob/9a206d41e5d2d0c241bb441f41eeadb945afaa55/src/gsm/run.py#L16) implements the loop; [src/gsm/feedback.py](https://github.com/madaan/self-refine/blob/9a206d41e5d2d0c241bb441f41eeadb945afaa55/src/gsm/feedback.py#L19) supplies the feedback prompt. |
| **Reflexion**, Shinn et al., NeurIPS 2023. [arXiv v4](https://arxiv.org/html/2303.11366v4) | `noahshinn/reflexion@218cf0ef1df84b05ce379dd4a8e47f17766733a0`; [MIT](https://github.com/noahshinn/reflexion/blob/218cf0ef1df84b05ce379dd4a8e47f17766733a0/LICENSE) | Verbal reflection conditions a later attempt. [programming_runs/reflexion.py](https://github.com/noahshinn/reflexion/blob/218cf0ef1df84b05ce379dd4a8e47f17766733a0/programming_runs/reflexion.py#L33) uses internal or visible tests for feedback and a separate execution evaluation against the real tests. |
| **Improving Factuality and Reasoning in Language Models through Multiagent Debate**, Du et al., ICML 2024. [arXiv v1](https://arxiv.org/abs/2305.14325v1) and [author project page](https://composable-models.github.io/llm_debate/) | `composable-models/llm_multiagent_debate@9846749350eb917ae5bfaaff4c645fc705b8d3af`; no explicit repository license found at this snapshot | [gsm/gen_gsm.py](https://github.com/composable-models/llm_multiagent_debate/blob/9846749350eb917ae5bfaaff4c645fc705b8d3af/gsm/gen_gsm.py#L32) uses three instances of `gpt-3.5-turbo-0301` and exchanges complete answers from the preceding round. These are symmetric agents, not a proposer and a dedicated independent judge. |
| **CRITIC**, Gou et al., ICLR 2024. [arXiv v4](https://arxiv.org/html/2305.11738v4) | `microsoft/ProphetNet@5cf70eb41cdaa1d8faa3e1265d95ee5792d49a53`; [MIT](https://github.com/microsoft/ProphetNet/blob/5cf70eb41cdaa1d8faa3e1265d95ee5792d49a53/LICENSE) | [CRITIC/src/program/critic.py](https://github.com/microsoft/ProphetNet/blob/5cf70eb41cdaa1d8faa3e1265d95ee5792d49a53/CRITIC/src/program/critic.py#L66) incorporates execution feedback into critique and then correction. [evaluate.py](https://github.com/microsoft/ProphetNet/blob/5cf70eb41cdaa1d8faa3e1265d95ee5792d49a53/CRITIC/src/program/evaluate.py#L10) distinguishes ordinary evaluation from an explicitly labeled oracle variant. |
| **Debate or Vote: Which Yields Better Decisions in Multi-Agent Large Language Models?**, Choi et al., NeurIPS 2025. [arXiv v1](https://arxiv.org/html/2508.17536v1) | `deeplearning-wisc/debate-or-vote@82c929ea773d534cfb3fb0ddc4b7d14d245ab549`; no explicit repository license found at this snapshot | [src/main.py](https://github.com/deeplearning-wisc/debate-or-vote/blob/82c929ea773d534cfb3fb0ddc4b7d14d245ab549/src/main.py#L72) separates peer responses from single-agent refinement. [src/evaluator.py](https://github.com/deeplearning-wisc/debate-or-vote/blob/82c929ea773d534cfb3fb0ddc4b7d14d245ab549/src/evaluator.py#L44) applies a common final-answer extraction protocol. |

Repository licensing above applies to the inspected code, not automatically to
papers, model weights, datasets, or hosted model services. Public availability is
not a substitute for an explicit code license. The unlicensed repositories are
mechanism references, not sources to vendor.

## Adoption and rejection decisions

| Source | Adopt for this experiment | Reject or limit | Consequence to check locally |
| --- | --- | --- | --- |
| Self-Refine | A genuine single-agent feedback and repair baseline; feedback can state that no supported problem was found. | Do not inherit the GSM prompt's presumption that an error exists. Do not let the model's correctness phrase certify success. Its `run.py:70-72` silently drops caught exceptions; this experiment retains them. | Correct candidates must survive unsupported criticism; errors and missing outputs remain in the denominator. |
| Reflexion | Distinguish evidence used for repair from final correctness evaluation. Keep concise public feedback available to a later attempt. | No cross-trial reflection memory. [immediate_reflexion.py](https://github.com/noahshinn/reflexion/blob/218cf0ef1df84b05ce379dd4a8e47f17766733a0/programming_runs/immediate_reflexion.py#L31) still generates a complete implementation before reflecting, so its name is not evidence for a design checkpoint benefit. | Report stage timing from actual calls and transitions, not the name of a method or a prompt. |
| Du et al. | Give reviewers an exact, frozen candidate and identifiable preceding decision; separate communication from final evaluation. | Consensus and same-model agreement are not truth. The pinned [gsm/eval_gsm.py:95-96](https://github.com/composable-models/llm_multiagent_debate/blob/9846749350eb917ae5bfaaff4c645fc705b8d3af/gsm/eval_gsm.py#L95) returns success when no predicted answer is parsed; that behavior must not carry over. | Missing and malformed responses cannot pass. A stale review cannot bind to a new base or delta. |
| CRITIC | Ground a challenge in a requested deterministic check and bind that evidence to the reviewed state. | The local model has no external tool access; the harness executes the declared check. This is not a reproduction of CRITIC's interactive tool setting. Do not use oracle labels to decide which attempts deserve repair, and do not expose final assessment as repair feedback. | Unsupported hard blocks are measured as errors; allowed early unknowns remain valid; actual protected-invariant violations are refused. |
| Debate or Vote | Keep extraction and evaluation identical across arms; inspect both improvement and regression. Separate interaction benefit from spending more computation. | Majority voting is not added to this two-factor experiment. The paper's martingale result depends on its model assumptions and is not a theorem about this fixture. Its implementation fixes agent/round counts and per-generation caps; it is not proof of matched total token cost. | Record realized cost and all calls alongside the shared budget. Do not infer a role benefit from a larger inference allowance or a formatting artifact. |

The upstream code observations identify behaviors in the cited snapshots. They do
not retrospectively establish how much any published aggregate result was affected.
In particular, a parser defect is a reason to test local failure accounting, not a
basis for inventing corrected paper scores.

## What current measurements can establish

The intended comparison is paired repeated execution of the same bounded fixture
under the same recorded model configuration. All proposer, reviewer, repair, and
retry calls belong to the trial budget. A shared ceiling controls opportunity; it
does not make realized cost equal. Results must show actual reported usage, elapsed
time, failures, and any usage that could not be recovered. CLI-reported equivalent
API cost is not necessarily a user's billed subscription charge.

Deterministic fixture tests establish mechanics: exact binding, stale criticism,
stage-appropriate unknowns, invariant refusal, bounded repair, timeout and budget
handling, and failure accounting. They do not establish model capability. Only
retained live provider calls can support a statement about model behavior, and a
small sample may remain inconclusive even when all harness tests pass.

The declared dependency graph supplies propagation depth for this fixture. The
actual changed dependent references supply repair scope. These quantities are
different: a potential downstream closure is not evidence that every dependent
decision was changed, and a failed or aborted trial is not a zero-propagation
success. Missed errors, omissions, malformed answers, provider errors, timeouts,
and budget exhaustion stay visible in the full trial denominator. Model agreement
or self-reported confidence never substitutes for those observations.

## Limits of transfer

- The fixture is synthetic and finite. It tests entry, gallery, access, protected
  courtyard, and declared dependency decisions; it does not establish architectural
  quality, code compliance, complete building dependencies, or usability in a real
  design session.
- The three synchronous checkpoints are decision boundaries. This study does not
  test interruption at every token or geometry primitive, asynchronous shadow
  critique, a general multi-agent system, or a new canonical write path.
- Separate Critic context can change anchoring and access to earlier public
  decisions. With one underlying model, correlated errors remain possible. The
  comparison cannot claim diversity of model training, knowledge, or judgment.
- Earlier review has more review opportunities under the same ceiling. That is
  part of the timing intervention, so realized review and repair counts must be
  reported rather than described as identical computation.
- Final checks are fixed, limited predicates rather than an independent architect.
  Shared predicate omissions can affect every arm. Architectural judgments outside
  that declared scope remain unknown.
- The cited papers use other models, prompts, datasets, feedback channels, and
  task structures. Positive or negative results from them cannot replace live
  repetitions under the same predeclared cost/call/time ceilings here, nor authorize
  a production superiority claim.

This note records the research rationale. Run records and their independent final
assessments determine the experiment's results, including negative or unsupported
conclusions.
