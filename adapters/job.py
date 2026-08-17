"""Vendor-neutral creative job manifest.

A CreativeJob is a small, versioned wrapper around the existing GenerationRequest
contract (adapters/base.py) — it does not replace it. It adds the things a real
production run needs that a single request doesn't carry: a stable job/execution ID,
an intended channel, where the approval-ready package lands, and a provenance seed
(who/why) that gets folded into the output provenance record. See
docs/adapter/DESIGN.md § 8 (Phase 3).

JSON on disk -> load_job() -> CreativeJob -> job.to_generation_request() ->
the same GenerationRequest every other adapter path already uses.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from adapters.base import GenerationRequest, ProductLock

_REPO_ROOT = Path(__file__).resolve().parent.parent

_VALID_FIDELITY = {"environment-only", "none"}


class JobValidationError(ValueError):
    """Raised when a job manifest is missing a required field or has an invalid value."""


@dataclass(frozen=True)
class ProductSpec:
    asset: Path
    fidelity: Literal["environment-only", "none"] = "environment-only"


@dataclass(frozen=True)
class BackgroundSpec:
    asset: Path


@dataclass(frozen=True)
class CompositeOptions:
    mode: str = "fit"
    position: str = "center"
    margin: float = 0.08


@dataclass(frozen=True)
class ProvenanceSeed:
    """Who/why metadata folded into the output provenance record. Never a secret."""

    requested_by: str = ""
    notes: str = ""


@dataclass(frozen=True)
class CreativeJob:
    job_id: str
    channel: str
    product: ProductSpec
    aspect_ratio: str
    output_dir: Path
    background: BackgroundSpec | None = None
    backend: str = "local_compositor"
    composite: CompositeOptions = field(default_factory=CompositeOptions)
    provenance_seed: ProvenanceSeed = field(default_factory=ProvenanceSeed)

    def product_lock(self) -> ProductLock | None:
        if self.product.fidelity == "none":
            return None
        return ProductLock(canonical_image=self.product.asset)

    def to_generation_request(self) -> GenerationRequest:
        return GenerationRequest(
            kind="image_edit",
            prompt=(
                f"job {self.job_id}: local composite for channel={self.channel!r} "
                "(no generative model — deterministic compositing only)"
            ),
            aspect_ratio=self.aspect_ratio,
            source=self.product.asset,
            background=self.background.asset if self.background else None,
            product_lock=self.product_lock(),
            n=1,
            output_dir=self.output_dir,
            model="deterministic-compositor-v1",
            options={
                "mode": self.composite.mode,
                "position": self.composite.position,
                "margin": self.composite.margin,
            },
        )


def _require(data: dict, key: str, job_file: Path) -> object:
    if key not in data:
        raise JobValidationError(f"{job_file}: missing required field {key!r}")
    return data[key]


def _resolve(raw_path: str, repo_root: Path) -> Path:
    p = Path(raw_path)
    return p if p.is_absolute() else (repo_root / p)


def load_job(job_file: Path, repo_root: Path | None = None) -> CreativeJob:
    """Parse and validate a job manifest JSON file.

    Relative asset/output paths in the manifest are resolved against repo_root
    (defaults to this repo's root) — the same convention every other path in this
    repo already uses (references/products/..., logs/..., etc.), not the manifest
    file's own directory.
    """
    root = repo_root or _REPO_ROOT
    if not job_file.exists():
        raise JobValidationError(f"job manifest not found: {job_file}")
    try:
        data = json.loads(job_file.read_text())
    except json.JSONDecodeError as e:
        raise JobValidationError(f"{job_file}: invalid JSON: {e}") from e
    if not isinstance(data, dict):
        raise JobValidationError(f"{job_file}: top-level JSON must be an object")

    job_id = _require(data, "job_id", job_file)
    channel = _require(data, "channel", job_file)
    aspect_ratio = _require(data, "aspect_ratio", job_file)
    output_dir_raw = _require(data, "output_dir", job_file)

    product_raw = _require(data, "product", job_file)
    if not isinstance(product_raw, dict) or "asset" not in product_raw:
        raise JobValidationError(f"{job_file}: 'product' must be an object with an 'asset' field")
    fidelity = product_raw.get("fidelity", "environment-only")
    if fidelity not in _VALID_FIDELITY:
        raise JobValidationError(
            f"{job_file}: product.fidelity must be one of {sorted(_VALID_FIDELITY)}, got {fidelity!r}"
        )
    product = ProductSpec(asset=_resolve(product_raw["asset"], root), fidelity=fidelity)

    background = None
    background_raw = data.get("background")
    if background_raw is not None:
        if not isinstance(background_raw, dict) or "asset" not in background_raw:
            raise JobValidationError(
                f"{job_file}: 'background' must be an object with an 'asset' field, or omitted"
            )
        background = BackgroundSpec(asset=_resolve(background_raw["asset"], root))

    backend = data.get("backend", "local_compositor")

    composite_raw = data.get("composite", {})
    if not isinstance(composite_raw, dict):
        raise JobValidationError(f"{job_file}: 'composite' must be an object")
    composite = CompositeOptions(
        mode=composite_raw.get("mode", "fit"),
        position=composite_raw.get("position", "center"),
        margin=float(composite_raw.get("margin", 0.08)),
    )

    seed_raw = data.get("provenance_seed", {})
    if not isinstance(seed_raw, dict):
        raise JobValidationError(f"{job_file}: 'provenance_seed' must be an object")
    provenance_seed = ProvenanceSeed(
        requested_by=str(seed_raw.get("requested_by", "")),
        notes=str(seed_raw.get("notes", "")),
    )

    return CreativeJob(
        job_id=str(job_id),
        channel=str(channel),
        product=product,
        aspect_ratio=str(aspect_ratio),
        output_dir=_resolve(str(output_dir_raw), root),
        background=background,
        backend=str(backend),
        composite=composite,
        provenance_seed=provenance_seed,
    )
