"""Descript backend — STUB, no executable request path yet.

Would cover: VIDEO_EDIT operations on a finished asset — transcription-based editing,
Overdub, filler-word removal, Studio Sound cleanup. Overlaps in role with this repo's
existing shared/skills/caption-video (which is already vendor-neutral, using
HyperFrames + Whisper + ffmpeg locally). Descript's public API surface is limited;
wiring this for real requires a Descript API token/app access and an implemented
request path, neither of which exists in this repo.
"""

from __future__ import annotations

from adapters.base import Capability
from adapters.stub_backend import UnimplementedBackend


class DescriptBackend(UnimplementedBackend):
    name = "descript"
    required_env = ("DESCRIPT_API_TOKEN",)
    declared_capabilities = frozenset({Capability.VIDEO_EDIT})
    setup_note = (
        "Needs Descript API access plus an implemented request path. Not present in "
        "this repo — shared/skills/caption-video already covers captioning locally "
        "without Descript."
    )
