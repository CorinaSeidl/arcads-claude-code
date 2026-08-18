"""OpenAI backend — STUB, no executable request path yet.

Would cover: gpt-image-1-family text-to-image and, importantly for the
product-fidelity rule (docs/adapter/DESIGN.md § 4.4), the `images/edits` endpoint —
the closest fit today for "preserve canonical product pixels, edit the environment
around them." Wiring this for real requires an OPENAI_API_KEY and testing against
real spend, neither of which this repo has.
"""

from __future__ import annotations

from adapters.base import Capability
from adapters.stub_backend import UnimplementedBackend


class OpenAIBackend(UnimplementedBackend):
    name = "openai"
    required_env = ("OPENAI_API_KEY",)
    declared_capabilities = frozenset({Capability.IMAGE, Capability.IMAGE_EDIT})
    setup_note = (
        "Needs OPENAI_API_KEY plus an implemented request path against the Images "
        "API (generations + edits). Not present in this repo."
    )
