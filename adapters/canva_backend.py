"""Canva backend — STUB, no executable request path yet.

Would cover: populating a Canva brand template via the Canva Connect API (OAuth) —
useful for compositing an approved product photo into a fixed layout without any
pixel regeneration at all, which is one valid way to satisfy the product-fidelity rule
(docs/adapter/DESIGN.md § 4.4). Wiring this for real requires a Canva Connect API
OAuth app/token and an implemented autofill/export request path, neither of which
exists in this repo.
"""

from __future__ import annotations

from adapters.base import Capability
from adapters.stub_backend import UnimplementedBackend


class CanvaBackend(UnimplementedBackend):
    name = "canva"
    required_env = ("CANVA_ACCESS_TOKEN",)
    declared_capabilities = frozenset({Capability.IMAGE_EDIT})
    setup_note = (
        "Needs a Canva Connect API OAuth token plus an implemented autofill/export "
        "request path against Canva's API. Not present in this repo."
    )
