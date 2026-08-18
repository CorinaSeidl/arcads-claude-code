"""Base class for honest backend stubs.

Per docs/adapter/DESIGN.md § 4.5 and the task constraint "do not claim a backend is
automated unless the repo has a real executable path": these backends declare
capabilities and credential requirements so the registry/docs can describe them, but
``generate()`` always raises NotImplementedError. Nothing here calls out to a network.

Wiring one of these for real is future work: pick a backend, get the user's actual
credentials for it, implement request-building against the vendor's real API, and test
it (the user pays for whatever it costs — this repo can't validate it for you).
"""

from __future__ import annotations

import os

from adapters.base import (
    Capability,
    CostEstimate,
    CreativeBackend,
    CredentialStatus,
    GenerationRequest,
    GenerationResult,
)
from adapters.policy import ProductFidelityGuard


class UnimplementedBackend(CreativeBackend):
    """Subclass and set the four class attributes below. Do not override generate()."""

    name: str = ""
    required_env: tuple[str, ...] = ()
    declared_capabilities: frozenset[Capability] = frozenset()
    setup_note: str = ""

    def capabilities(self) -> frozenset[Capability]:
        return self.declared_capabilities

    def check_credentials(self) -> CredentialStatus:
        missing = [var for var in self.required_env if not os.environ.get(var)]
        if missing:
            return CredentialStatus(
                False,
                f"{self.name}: missing env var(s) {', '.join(missing)}. {self.setup_note}",
            )
        return CredentialStatus(
            False,  # credentials present doesn't mean "usable" — there's still no code path
            f"{self.name}: required env var(s) present, but this backend has no "
            "implemented request path yet in this repo (see adapters/README.md).",
        )

    def estimate_cost(self, request: GenerationRequest) -> CostEstimate | None:
        return None  # no vendor integration to quote against

    def generate(self, request: GenerationRequest) -> list[GenerationResult]:
        # Defense-in-depth, same as ArcadsBackend — checked before the "not implemented"
        # path so future real implementations inherit the guard automatically.
        if request.product_lock is not None:
            ProductFidelityGuard.check(self, request)
        raise NotImplementedError(
            f"{self.name} backend is not implemented in this repo yet. "
            f"{self.setup_note} See docs/adapter/DESIGN.md § 4.5 / § 6 and "
            "adapters/README.md for what wiring it up for real would take."
        )
