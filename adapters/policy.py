"""Backend-independent policy: enforced once, above every adapter.

These encode the process guarantees that already exist as prose in
skills/arcads-external-api/SKILL.md (QA retry cap, cost confirmation gate, mandatory
still-before-video approval, provenance logging) as code, so a new backend can't
silently skip them. See docs/adapter/DESIGN.md § 4.3.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path

from adapters.base import (
    Capability,
    CreativeBackend,
    GenerationRequest,
    GenerationResult,
    ProductFidelityViolation,
)

DEFAULT_MAX_QA_RETRIES = 2  # 2 retries = 3 attempts total, matching the existing Arcads rule


@dataclass
class RetryPolicy:
    """Bounds automatic QA-fix regeneration. Does not bound user-directed re-runs."""

    max_retries: int = DEFAULT_MAX_QA_RETRIES
    attempts_made: int = 0

    def may_retry(self) -> bool:
        return self.attempts_made < self.max_retries

    def record_attempt(self) -> None:
        self.attempts_made += 1


class CostConfirmationRequired(RuntimeError):
    """Raised by CostGate.require() when the caller hasn't confirmed spend yet."""


@dataclass
class CostGate:
    """First generation in a batch needs explicit confirmation; QA-fix retries don't
    (but are still logged/billed) — mirrors the documented Arcads exception."""

    confirmed: bool = False

    def require(self, *, is_qa_retry: bool = False) -> None:
        if is_qa_retry:
            return
        if not self.confirmed:
            raise CostConfirmationRequired(
                "cost estimate not confirmed — show the estimate and get explicit "
                "user confirmation before calling generate()"
            )

    def confirm(self) -> None:
        self.confirmed = True


def require_still_before_video(
    request: GenerationRequest, *, still_was_approved: bool
) -> None:
    """Refuses to build a video request animating an unapproved still.

    Mirrors "Never skip the approval step" in skills/arcads-external-api/SKILL.md's
    influencer-recreation and product-showcase flows.
    """
    if request.kind == "video" and request.start_frame is not None and not still_was_approved:
        raise PermissionError(
            "refusing to animate start_frame "
            f"{request.start_frame} — the still has not been marked approved. "
            "Show it to the user and get explicit approval first."
        )


class ProductFidelityGuard:
    """Enforces the hard product-fidelity rule (docs/adapter/DESIGN.md § 4.4):
    if a request carries a ProductLock, it must route through a backend/capability
    that preserves the canonical product pixels — never full text-to-image regeneration
    of a labeled/branded product.
    """

    @staticmethod
    def check(backend: CreativeBackend, request: GenerationRequest) -> None:
        if request.product_lock is None:
            return
        if not request.product_lock.canonical_image.exists():
            raise ProductFidelityViolation(
                f"product_lock.canonical_image does not exist: "
                f"{request.product_lock.canonical_image}"
            )
        if Capability.IMAGE_EDIT not in backend.capabilities():
            raise ProductFidelityViolation(
                f"backend {backend.name!r} cannot honor product_lock — it has no "
                "IMAGE_EDIT capability, so it cannot preserve canonical product pixels "
                "while editing the surrounding environment. Pick a backend that "
                "supports image editing/inpainting, or drop product_lock only if the "
                "user explicitly accepts the product itself being regenerated."
            )
        if request.kind != "image_edit":
            raise ProductFidelityViolation(
                f"request.kind={request.kind!r} with a product_lock set — "
                "product-locked requests must use kind='image_edit' with "
                "source=product_lock.canonical_image, never plain text-to-image."
            )


def sha256_of(path: Path) -> str | None:
    """sha256 of a file's bytes, or None if it doesn't exist. Shared by provenance
    builders across backends (e.g. adapters/local_compositor_backend.py)."""
    if not path.exists():
        return None
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def build_provenance(
    result: GenerationResult,
    request: GenerationRequest,
) -> dict:
    """Provenance record for a single result. Never includes secrets or the raw prompt
    (only a hash) — same discipline as logs/arcads-api.jsonl's existing convention."""
    prompt_hash = hashlib.sha256(request.prompt.encode("utf-8")).hexdigest()
    return {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "backend": result.backend,
        "model": result.model,
        "kind": request.kind,
        "variant": result.variant,
        "output_path": str(result.path),
        "output_sha256": sha256_of(result.path),
        "prompt_sha256": prompt_hash,
        "prompt_word_count": len(request.prompt.split()),
        "reference_count": len(request.references),
        "product_locked": request.product_lock is not None,
        "cost": (
            {"amount": result.cost.amount, "unit": result.cost.unit, "source": result.cost.source}
            if result.cost
            else None
        ),
    }


def write_provenance(record: dict, log_path: Path) -> None:
    """Append one JSON line, generalizing the logs/arcads-api.jsonl pattern to any
    backend. Does not overwrite prior entries; creates parent dirs as needed."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
