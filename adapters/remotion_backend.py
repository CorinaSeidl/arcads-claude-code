"""Remotion backend — STUB, no executable request path yet.

Remotion is a code-driven video compositor (React + Node), not a generative-AI API —
closer in kind to this repo's existing shared/skills/caption-video (HyperFrames +
ffmpeg) than to Arcads/OpenAI/Runway. It would cover VIDEO_EDIT: stitching, captioning,
and programmatic composition of already-generated clips, run locally via `npx remotion`
(no API key required for local rendering). Remotion Lambda (optional cloud rendering)
would additionally need AWS credentials. Neither the local Node project scaffold nor
an implemented request path exists in this repo yet.
"""

from __future__ import annotations

from adapters.base import Capability
from adapters.stub_backend import UnimplementedBackend


class RemotionBackend(UnimplementedBackend):
    name = "remotion"
    required_env = ()  # local rendering needs Node.js + `npx remotion`, not an API key
    declared_capabilities = frozenset({Capability.VIDEO_EDIT})
    setup_note = (
        "Needs a local Remotion project (Node.js + `npx remotion`) and an implemented "
        "composition/render pipeline. Optional cloud rendering via Remotion Lambda "
        "would additionally need AWS credentials. Not present in this repo."
    )
