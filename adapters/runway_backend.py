"""Runway backend — STUB, no executable request path yet.

Would cover: image-to-video and text-to-video generation. Wiring this for real
requires a Runway API key/secret plus an implemented request path against Runway's
API, neither of which this repo has.
"""

from __future__ import annotations

from adapters.base import Capability
from adapters.stub_backend import UnimplementedBackend


class RunwayBackend(UnimplementedBackend):
    name = "runway"
    required_env = ("RUNWAY_API_KEY",)
    declared_capabilities = frozenset({Capability.VIDEO})
    setup_note = (
        "Needs a Runway API key plus an implemented request path against Runway's "
        "video generation API. Not present in this repo."
    )
