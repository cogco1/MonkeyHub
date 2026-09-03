"""Exact, no-fallback dispatch for pluggable search policies.

The registry selects only an explicitly requested policy id.  It does not
choose a policy family, retry a failed implementation, or substitute a
different algorithm.  Returned directives remain persistence-neutral and are
validated against the exact request before they leave this boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from archive.archflow.control.search_policy import (
    AsyncSearchPolicy,
    SearchDirective,
    SearchPolicyDescriptor,
    SearchPolicyRequest,
    validate_search_directive,
)


class SearchPolicyRegistryError(RuntimeError):
    """A policy registration or dispatch boundary was violated."""


class SearchPolicyUnavailableError(SearchPolicyRegistryError):
    """The exact requested policy implementation is not registered."""


@dataclass(frozen=True, slots=True)
class _RegisteredSearchPolicy:
    implementation: AsyncSearchPolicy
    descriptor: SearchPolicyDescriptor


class SearchPolicyRegistry:
    """In-memory registry for exact policy implementations.

    Registration is explicit and instance-local.  In particular, naming the
    reserved ``ocba`` family in a request does not create an implementation;
    a team-owned adapter must be registered under the exact policy id first.
    """

    def __init__(
        self,
        policies: Iterable[AsyncSearchPolicy] = (),
    ) -> None:
        self._policies: dict[str, _RegisteredSearchPolicy] = {}
        for policy in policies:
            self.register(policy)

    def register(self, policy: AsyncSearchPolicy) -> None:
        if not isinstance(policy, AsyncSearchPolicy):
            raise TypeError("policy must implement AsyncSearchPolicy")
        descriptor = policy.descriptor
        if not isinstance(descriptor, SearchPolicyDescriptor):
            raise TypeError(
                "policy descriptor must be SearchPolicyDescriptor"
            )
        if descriptor.policy_id in self._policies:
            raise SearchPolicyRegistryError(
                f"search policy id is already registered: "
                f"{descriptor.policy_id}"
            )
        descriptor_snapshot = SearchPolicyDescriptor.from_dict(
            descriptor.to_dict()
        )
        self._policies[descriptor.policy_id] = _RegisteredSearchPolicy(
            implementation=policy,
            descriptor=descriptor_snapshot,
        )

    @property
    def descriptors(self) -> tuple[SearchPolicyDescriptor, ...]:
        return tuple(
            self._policies[policy_id].descriptor
            for policy_id in sorted(self._policies)
        )

    def require(self, descriptor: SearchPolicyDescriptor) -> AsyncSearchPolicy:
        if not isinstance(descriptor, SearchPolicyDescriptor):
            raise TypeError("descriptor must be SearchPolicyDescriptor")
        registered = self._policies.get(descriptor.policy_id)
        if registered is None:
            raise SearchPolicyUnavailableError(
                f"exact search policy is not registered: "
                f"{descriptor.policy_id}"
            )
        current_descriptor = registered.implementation.descriptor
        if (
            current_descriptor != registered.descriptor
            or descriptor != registered.descriptor
        ):
            raise SearchPolicyRegistryError(
                "registered search policy descriptor changed or was misbound"
            )
        return registered.implementation

    async def decide(
        self,
        request: SearchPolicyRequest,
    ) -> SearchDirective:
        if not isinstance(request, SearchPolicyRequest):
            raise TypeError("request must be SearchPolicyRequest")
        implementation = self.require(request.policy)
        directive = await implementation.decide(request)
        return validate_search_directive(request, directive)


__all__ = [
    "SearchPolicyRegistry",
    "SearchPolicyRegistryError",
    "SearchPolicyUnavailableError",
]
