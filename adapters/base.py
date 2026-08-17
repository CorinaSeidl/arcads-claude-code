"""Backend-agnostic request/result types and the CreativeBackend contract.

Every backend adapter (Arcads today; OpenAI/Runway/Remotion/Canva/Descript as future
stubs) implements CreativeBackend so that skills, QA, cost display, and provenance
logging can stay backend-independent. See docs/adapter/DESIGN.md § 4.2.
"""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


class Capability(enum.Enum):
    """What a backend can produce. A backend may support any subset."""

    IMAGE = "image"  # text-to-image
    IMAGE_EDIT = "image_edit"  # edit/inpaint an existing image, preserving unmasked pixels
    VIDEO = "video"  # text-to-video or image-to-video
    VIDEO_EDIT = "video_edit"  # e.g. captioning, trimming, remixing an existing video


@dataclass(frozen=True)
class ProductLock:
    """Marks a request as requiring pixel-exact product fidelity.

    See docs/adapter/DESIGN.md § 4.4 — the hard rule. When set, the request MUST be
    routed through a backend capability that preserves the canonical product pixels
    (IMAGE_EDIT / inpaint-around-region), never a full text-to-image regeneration of
    the product itself. Only "environment-only" is supported in phase 1: the model may
    generate/edit everything EXCEPT the product region.
    """

    canonical_image: Path
    mode: Literal["environment-only"] = "environment-only"

    def __post_init__(self) -> None:
        if self.mode != "environment-only":
            raise ValueError(f"unsupported ProductLock mode: {self.mode!r}")


@dataclass(frozen=True)
class GenerationRequest:
    kind: Literal["image", "image_edit", "video"]
    prompt: str
    aspect_ratio: str | None = None
    references: tuple[Path, ...] = ()
    source: Path | None = None  # required for kind == "image_edit"
    product_lock: ProductLock | None = None
    n: int = 1
    output_dir: Path = Path("./generated")
    start_frame: Path | None = None  # approved still used to start a video (still-before-video)
    model: str | None = None  # backend-specific model hint; interpretation is per-backend

    def __post_init__(self) -> None:
        if self.kind == "image_edit" and self.source is None:
            raise ValueError("GenerationRequest(kind='image_edit') requires source")
        if self.kind == "video" and self.product_lock is not None:
            # Phase 1 scope: product-fidelity guard only understands image edits today.
            raise ValueError(
                "product_lock on a video request is not supported yet — "
                "generate the product-locked still first, then animate it as start_frame"
            )
        if not (1 <= self.n <= 10):
            raise ValueError(f"n must be 1..10, got {self.n}")


@dataclass(frozen=True)
class CostEstimate:
    amount: float
    unit: Literal["credits", "usd"]
    source: Literal["historical_log", "user_provided", "backend_quote", "unknown"]
    note: str = ""

    def __str__(self) -> str:  # human-facing, always shows the unit — never normalize silently
        base = f"~{self.amount:g} {self.unit}"
        return f"{base} ({self.note})" if self.note else base


@dataclass(frozen=True)
class CredentialStatus:
    ok: bool
    detail: str  # human-readable: what's missing, or "credentials found"


@dataclass(frozen=True)
class GenerationResult:
    variant: int
    path: Path
    backend: str
    model: str
    cost: CostEstimate | None
    provenance: dict = field(default_factory=dict)


class ProductFidelityViolation(RuntimeError):
    """Raised when a product_lock request can't be routed to a fidelity-preserving mode."""


class CreativeBackend(ABC):
    """Contract every backend adapter must satisfy."""

    name: str

    @abstractmethod
    def capabilities(self) -> frozenset[Capability]:
        """What this backend can do. Never raises."""

    @abstractmethod
    def check_credentials(self) -> CredentialStatus:
        """Never raises — reports whether this backend is currently usable."""

    @abstractmethod
    def estimate_cost(self, request: GenerationRequest) -> CostEstimate | None:
        """Best-effort cost estimate. None means: unknown, caller must ask the user."""

    @abstractmethod
    def generate(self, request: GenerationRequest) -> list[GenerationResult]:
        """Execute the request. Real backends call out to a vendor; stubs raise
        NotImplementedError — see adapters/README.md."""
