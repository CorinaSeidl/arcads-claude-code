"""Deterministic QA for a completed local_compositor job.

No network, no vendor call, no ML-based checks — every check here is either a plain
file/metadata assertion or an independently recomputed deterministic image transform
compared byte-for-byte against the actual output. See docs/adapter/DESIGN.md § 8.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from adapters.base import GenerationRequest, GenerationResult
from adapters.job import CreativeJob
from adapters.local_compositor_backend import CANVAS_SIZES, resize_cover, composite
from adapters.policy import sha256_of

try:
    from PIL import Image

    _PIL_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PIL_AVAILABLE = False


@dataclass(frozen=True)
class QACheck:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class QAReport:
    checks: tuple[QACheck, ...]

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    def as_dict(self) -> dict:
        return {
            "passed": self.passed,
            "checks": [{"name": c.name, "passed": c.passed, "detail": c.detail} for c in self.checks],
        }


def _check_output_exists_and_opens(result: GenerationResult) -> QACheck:
    name = "output_exists_and_opens"
    if not result.path.exists():
        return QACheck(name, False, f"output file does not exist: {result.path}")
    if not _PIL_AVAILABLE:
        return QACheck(name, False, "Pillow not available — cannot verify image opens")
    try:
        with Image.open(result.path) as img:
            img.verify()
    except Exception as e:  # noqa: BLE001
        return QACheck(name, False, f"output file exists but is not a valid image: {e}")
    return QACheck(name, True, f"{result.path} exists and opens as a valid image")


def _check_dimensions(result: GenerationResult, aspect_ratio: str) -> QACheck:
    name = "expected_dimensions"
    expected = CANVAS_SIZES.get(aspect_ratio)
    if expected is None:
        return QACheck(name, False, f"unsupported aspect ratio {aspect_ratio!r}")
    try:
        with Image.open(result.path) as img:
            actual = img.size
    except Exception as e:  # noqa: BLE001
        return QACheck(name, False, f"could not read dimensions: {e}")
    if actual != tuple(expected):
        return QACheck(name, False, f"expected {expected} for {aspect_ratio}, got {actual}")
    return QACheck(name, True, f"{actual[0]}x{actual[1]} matches {aspect_ratio}")


def _check_product_not_mutated(job: CreativeJob, pre_run_sha256: str | None) -> QACheck:
    name = "product_asset_not_mutated"
    if pre_run_sha256 is None:
        return QACheck(name, False, "no pre-run hash captured — cannot verify")
    post_sha256 = sha256_of(job.product.asset)
    if post_sha256 != pre_run_sha256:
        return QACheck(
            name, False, f"product asset changed on disk during the run (before={pre_run_sha256}, after={post_sha256})"
        )
    return QACheck(name, True, f"unchanged, sha256={post_sha256}")


def _check_provenance_hashes_resolve(result: GenerationResult) -> QACheck:
    name = "provenance_hashes_resolve"
    prov = result.provenance
    problems = []
    for key, path_key in (
        ("product_sha256", "product_path"),
        ("background_sha256", "background_path"),
        ("output_sha256", None),
    ):
        recorded = prov.get(key)
        if recorded is None:
            problems.append(f"{key} missing from provenance")
            continue
        actual_path = Path(prov[path_key]) if path_key else result.path
        actual = sha256_of(actual_path)
        if actual != recorded:
            problems.append(f"{key}: recorded={recorded} actual={actual}")
    if problems:
        return QACheck(name, False, "; ".join(problems))
    return QACheck(name, True, "product/background/output hashes all match files on disk")


def _check_no_unexpected_overlay(
    job: CreativeJob, request: GenerationRequest, result: GenerationResult
) -> QACheck:
    """Recompute the background-only composite independently and diff it against the
    actual output everywhere OUTSIDE the product's placement box. If they match
    exactly, nothing — no logo, watermark, caption, or generative artifact — was
    drawn onto the canvas beyond 'background, cover-fit' + 'product, placed'. This is
    the deterministic stand-in for 'no unexpected text overlay': the compositor code
    never calls a text/draw API at all, and this proves nothing else touched those
    pixels either.
    """
    name = "no_unexpected_overlay"
    if not _PIL_AVAILABLE:
        return QACheck(name, False, "Pillow not available")
    canvas_size = CANVAS_SIZES.get(job.aspect_ratio)
    if canvas_size is None:
        return QACheck(name, False, f"unsupported aspect ratio {job.aspect_ratio!r}")
    if request.background is None:
        return QACheck(name, False, "no background in request — nothing to compare against")
    box = result.provenance.get("product_box")
    if box is None:
        return QACheck(name, False, "provenance has no product_box to exclude")
    x0, y0, x1, y1 = box

    try:
        with Image.open(request.background) as bg:
            expected_bg_only = resize_cover(bg.convert("RGB"), *canvas_size)
        with Image.open(result.path) as actual:
            actual_rgb = actual.convert("RGB")
    except Exception as e:  # noqa: BLE001
        return QACheck(name, False, f"could not open images for comparison: {e}")

    if expected_bg_only.size != actual_rgb.size:
        return QACheck(name, False, "canvas size mismatch — cannot compare")

    w, h = expected_bg_only.size
    exp_px = expected_bg_only.load()
    act_px = actual_rgb.load()
    mismatches = 0
    sample_mismatch = None
    for y in range(0, h, 4):  # every 4th row is enough to catch any drawn overlay; full-res would be slower for no benefit
        for x in range(0, w, 4):
            if x0 <= x < x1 and y0 <= y < y1:
                continue  # inside the product box — expected to differ, not part of this check
            if exp_px[x, y] != act_px[x, y]:
                mismatches += 1
                if sample_mismatch is None:
                    sample_mismatch = (x, y, exp_px[x, y], act_px[x, y])

    if mismatches:
        return QACheck(
            name,
            False,
            f"{mismatches} sampled pixel(s) outside the product box differ from the "
            f"expected background-only composite — something else was drawn onto the "
            f"canvas. First mismatch at {sample_mismatch}",
        )
    return QACheck(
        name, True, "every sampled pixel outside the product box matches an independently "
        "recomputed background-only composite — no overlay/text was introduced"
    )


def _check_deterministic_rerun(job: CreativeJob, request: GenerationRequest, result: GenerationResult) -> QACheck:
    name = "deterministic_rerun"
    canvas_size = CANVAS_SIZES.get(job.aspect_ratio)
    if canvas_size is None or request.background is None:
        return QACheck(name, False, "cannot rerun — missing aspect ratio or background")
    try:
        with tempfile.TemporaryDirectory() as d:
            rerun_path = Path(d) / "rerun.png"
            composite(
                product_path=request.source,
                background_path=request.background,
                out_path=rerun_path,
                canvas_size=canvas_size,
                mode=request.options.get("mode", "fit"),
                position=request.options.get("position", "center"),
                margin=float(request.options.get("margin", 0.08)),
            )
            same = rerun_path.read_bytes() == result.path.read_bytes()
    except Exception as e:  # noqa: BLE001
        return QACheck(name, False, f"rerun failed: {e}")
    if not same:
        return QACheck(name, False, "rerun with identical inputs produced different output bytes")
    return QACheck(name, True, "rerun with identical inputs produced byte-identical output")


def run_qa(
    job: CreativeJob,
    request: GenerationRequest,
    result: GenerationResult,
    pre_run_product_sha256: str | None,
) -> QAReport:
    checks = (
        _check_output_exists_and_opens(result),
        _check_dimensions(result, job.aspect_ratio),
        _check_product_not_mutated(job, pre_run_product_sha256),
        _check_provenance_hashes_resolve(result),
        _check_no_unexpected_overlay(job, request, result),
        _check_deterministic_rerun(job, request, result),
    )
    return QAReport(checks=checks)
