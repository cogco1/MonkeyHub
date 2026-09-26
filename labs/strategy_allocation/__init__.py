"""GH-268: state-conditioned allocation of rollouts across LLM strategies (research lab, V0).

See README.md. Interfaces: ``strategies.Strategy`` / ``strategies.Runner`` (strategy side),
``allocator.AllocationRule`` (algorithm side), ``environment.Environment`` (the finite cases),
``evaluator.evaluate`` (external outcome) and ``rollout.RolloutRecord`` (retained data).
"""
