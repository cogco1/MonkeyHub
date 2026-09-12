"""Content-free text estimates for the enforced application request budget.

Section strings describe disjoint text actually sent by a provider adapter.
Contributor strings are optional subsets for diagnosis, never additional input.
Tokenizers can be injected without introducing an SDK or network dependency.
Even tokenized text is an estimate of provider input: message framing and image
tokens are not known here. Billed usage remains on the invocation receipt.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from typing import Callable, Mapping


_SECTIONS = ("intent", "system", "schema", "state", "preferences", "dependencies", "overhead")
_CONTRIBUTORS = frozenset(_SECTIONS) | {
    "all_components", "type_registry", "relations", "parameters", "component_definitions",
    "levels", "grids", "readings", "entities", "constraints",
}
_TASK_TYPES = {"scalar", "component", "design", "scalar_edit", "component_edit", "design_edit", "unknown"}


def _nonnegative(value: object, name: str, *, optional: bool = False) -> None:
    if optional and value is None:
        return
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


def _heuristic_tokens(text: str) -> int:
    """A coarse text estimate; explicitly not a vendor tokenizer or billing count."""

    return (len(text.encode("utf-8")) + 3) // 4


@dataclass(frozen=True, slots=True)
class ContextBudget:
    model: str
    task_type: str
    estimator: str
    section_tokens: tuple[tuple[str, int], ...]
    largest_contributors: tuple[tuple[str, int], ...]
    budget_tokens: int
    expected_max_output_tokens: int | None
    image_count: int

    @property
    def estimated_input_tokens(self) -> int:
        return sum(count for _, count in self.section_tokens)

    @property
    def exceeded(self) -> bool:
        return self.estimated_input_tokens > self.budget_tokens

    def limitation(self) -> str | None:
        """An over-budget request cannot be sent or silently shortened."""
        if not self.exceeded:
            return None
        return (
            f"The required application context is estimated at {self.estimated_input_tokens} text tokens, "
            f"above the configured limit of {self.budget_tokens}. Narrow the request to a selected "
            "object or a smaller named area. Required design constraints have been retained; "
            "this request was not sent to the model. This limit excludes CLI/provider overhead "
            "and image token costs."
        )

    def to_details(self) -> dict[str, object]:
        """Bounded diagnostic metadata; the supplied source text is never retained."""

        return {
            "task_type": self.task_type,
            "context_budget": {
                "basis": "text_estimate", "estimator": self.estimator,
                "section_tokens": dict(self.section_tokens),
                "estimated_input_tokens": self.estimated_input_tokens,
                "budget_tokens": self.budget_tokens, "exceeded": self.exceeded,
                "expected_max_output_tokens": self.expected_max_output_tokens,
                "image_count": self.image_count, "image_tokens": None,
                "provider_overhead_included": False,
                "largest_contributors": [
                    {"name": name, "estimated_tokens": count}
                    for name, count in self.largest_contributors
                ],
            },
        }

    def log_preflight(self, logger: logging.Logger) -> None:
        """Log estimates; the caller enforces the budget before provider invocation."""

        sections = ", ".join(f"{name}={count}" for name, count in self.section_tokens)
        largest = ", ".join(f"{name}={count}" for name, count in self.largest_contributors)
        try:
            logger.log(
                logging.WARNING if self.exceeded else logging.INFO,
                "MONKEY REQUEST%s model=%s task_type=%s text_estimate=%s budget=%s "
                "estimator=%s sections=[%s] largest_contributors=[%s] "
                "expected_max_output=%s images=%s; provider framing and image tokens excluded",
                " context budget exceeded" if self.exceeded else "",
                self.model, self.task_type, self.estimated_input_tokens, self.budget_tokens,
                self.estimator, sections, largest, self.expected_max_output_tokens, self.image_count,
            )
        except Exception:
            # A diagnostic handler must not prevent a requested model call.
            pass


def build_context_budget(
    sections: Mapping[str, str], *, model: str, task_type: str,
    budget_tokens: int = 16_000, expected_max_output_tokens: int | None = None,
    contributors: Mapping[str, str] | None = None, image_count: int = 0,
    count_tokens: Callable[[str], int] | None = None, estimator: str | None = None,
) -> ContextBudget:
    """Estimate one request without retaining, altering or dropping its content.

    ``sections`` must be disjoint, already serialized request text; include any
    known application wrapper text as ``overhead``. Unspecified sections are
    empty. The default UTF-8-bytes/4 heuristic is deliberately labeled; a host
    with a suitable tokenizer may pass its counter and short estimator name.
    """

    if not isinstance(sections, Mapping) or set(sections) - set(_SECTIONS):
        raise ValueError("unsupported context section")
    if not isinstance(task_type, str) or task_type not in _TASK_TYPES:
        raise ValueError("unsupported context task type")
    if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,160}", model):
        raise ValueError("model must be a bounded model identifier")
    _nonnegative(budget_tokens, "budget_tokens")
    _nonnegative(expected_max_output_tokens, "expected_max_output_tokens", optional=True)
    _nonnegative(image_count, "image_count")
    if count_tokens is None:
        if estimator is not None:
            raise ValueError("an estimator name requires its token counter")
        count_tokens = _heuristic_tokens
        estimator = "heuristic_utf8_bytes_div4"
    else:
        estimator = estimator or "injected_text_tokenizer"
        if not isinstance(estimator, str) or not re.fullmatch(r"[a-zA-Z0-9_.:-]{1,80}", estimator):
            raise ValueError("estimator must be a bounded tokenizer identifier")

    def count(text: str) -> int:
        if not isinstance(text, str):
            raise ValueError("context section and contributor content must be text")
        value = count_tokens(text) if text else 0
        _nonnegative(value, "token estimate")
        return value

    totals = tuple((name, count(sections.get(name, ""))) for name in _SECTIONS)
    if contributors is not None:
        if not isinstance(contributors, Mapping) or set(contributors) - _CONTRIBUTORS:
            raise ValueError("unsupported context contributor")
        ranked = [(name, count(text)) for name, text in contributors.items()]
    else:
        ranked = list(totals)
    largest = tuple(sorted((item for item in ranked if item[1]), key=lambda item: (-item[1], item[0]))[:5])
    return ContextBudget(model, task_type, estimator, totals, largest, budget_tokens,
                         expected_max_output_tokens, image_count)
