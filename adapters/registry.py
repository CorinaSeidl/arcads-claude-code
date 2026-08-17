"""Backend registry — the single place skills should look up a CreativeBackend.

See docs/adapter/DESIGN.md § 4.5.
"""

from __future__ import annotations

from adapters.arcads_backend import ArcadsBackend
from adapters.base import CreativeBackend
from adapters.canva_backend import CanvaBackend
from adapters.descript_backend import DescriptBackend
from adapters.openai_backend import OpenAIBackend
from adapters.remotion_backend import RemotionBackend
from adapters.runway_backend import RunwayBackend

BACKENDS: dict[str, type[CreativeBackend]] = {
    "arcads": ArcadsBackend,
    "openai": OpenAIBackend,
    "runway": RunwayBackend,
    "remotion": RemotionBackend,
    "canva": CanvaBackend,
    "descript": DescriptBackend,
}


def get_backend(name: str) -> CreativeBackend:
    try:
        cls = BACKENDS[name]
    except KeyError as e:
        raise KeyError(
            f"unknown backend {name!r}. Known backends: {sorted(BACKENDS)}"
        ) from e
    return cls()


def list_backends() -> list[dict]:
    """Status summary for every registered backend — for a CLI/agent to display."""
    out = []
    for name, cls in sorted(BACKENDS.items()):
        backend = cls()
        cred = backend.check_credentials()
        out.append(
            {
                "name": name,
                "capabilities": sorted(c.value for c in backend.capabilities()),
                "credentials_ok": cred.ok,
                "detail": cred.detail,
                "executable": name == "arcads",  # only honest "real" backend today
            }
        )
    return out
