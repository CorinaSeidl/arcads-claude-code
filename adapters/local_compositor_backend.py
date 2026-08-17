"""local_compositor — a real, credential-free, deterministic backend.

Does NOT call any generative model. It takes a canonical local product image and a
locally-supplied background image (produced by any external generator, a
photographer, or hand-drawn — this backend does not care and never fetches one
itself) and deterministically composites them onto a standard social-media canvas:
resize/crop the background to fit, place the product on top, write a PNG, and record
provenance. Every operation is a plain geometric transform (resize/crop/paste) — never
a generative redraw — so it is the natural way to satisfy the product-fidelity rule
(docs/adapter/PRODUCT_FIDELITY.md) without needing any vendor at all.

Uses Pillow (the one non-stdlib dependency in this package — see adapters/README.md
"Dependencies"). Everything here is deterministic: the same inputs + params always
produce byte-identical output.
"""

from __future__ import annotations

from pathlib import Path

from adapters.base import (
    Capability,
    CostEstimate,
    CreativeBackend,
    CredentialStatus,
    GenerationRequest,
    GenerationResult,
)
from adapters.policy import ProductFidelityGuard, sha256_of

try:
    from PIL import Image

    _PIL_AVAILABLE = True
    _PIL_IMPORT_ERROR = ""
except ImportError as e:  # pragma: no cover - exercised only when Pillow is absent
    _PIL_AVAILABLE = False
    _PIL_IMPORT_ERROR = str(e)

# Standard social canvas sizes. Deliberately not limited to Arcads' 1:1/16:9/9:16
# cap (reference.md) — this backend has no vendor aspect-ratio constraint.
CANVAS_SIZES: dict[str, tuple[int, int]] = {
    "1:1": (1080, 1080),
    "9:16": (1080, 1920),
    "16:9": (1920, 1080),
    "4:5": (1080, 1350),
    "5:4": (1350, 1080),
}

_MODEL_NAME = "deterministic-compositor-v1"  # not an AI model — a fixed algorithm version


class CompositorError(RuntimeError):
    pass


def _resize_cover(img: "Image.Image", target_w: int, target_h: int) -> "Image.Image":
    """Resize (Lanczos) + center-crop so img exactly fills target_w x target_h,
    matching CSS `background-size: cover` semantics. Deterministic for fixed inputs."""
    src_w, src_h = img.size
    scale = max(target_w / src_w, target_h / src_h)
    new_w, new_h = max(1, round(src_w * scale)), max(1, round(src_h * scale))
    resized = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    return resized.crop((left, top, left + target_w, top + target_h))


def _position_box(
    position: str, canvas_w: int, canvas_h: int, paste_w: int, paste_h: int
) -> tuple[int, int]:
    if position == "center":
        return (canvas_w - paste_w) // 2, (canvas_h - paste_h) // 2
    if position == "bottom":
        margin_bottom = int(canvas_h * 0.04)
        return (canvas_w - paste_w) // 2, canvas_h - paste_h - margin_bottom
    raise CompositorError(f"unknown position {position!r} (expected 'center' or 'bottom')")


def composite(
    product_path: Path,
    background_path: Path,
    out_path: Path,
    canvas_size: tuple[int, int],
    mode: str = "fit",
    position: str = "center",
    margin: float = 0.08,
) -> dict:
    """Deterministically composite product_path onto background_path at canvas_size.

    mode='native': product is pasted at its original resolution — pixel-exact,
        never resampled. Raises CompositorError if the product doesn't fit; this is
        intentional (silently downscaling would break the "exact pixels" guarantee).
    mode='fit': product is resized (Lanczos, deterministic) to fit within
        (1 - 2*margin) of the canvas, only if it's larger than that budget — never
        upscaled. Not pixel-exact when resizing occurs; this is recorded in the
        returned dict (`product_pixels_exact: False`) and in provenance, so it is
        never confused with an AI redraw — it's a documented, reproducible transform.

    Returns a dict describing what happened (for provenance): exact-pixel flag,
    the product's placement box on the canvas, and the resolved canvas size.
    """
    if not _PIL_AVAILABLE:
        raise CompositorError(f"Pillow is not installed: {_PIL_IMPORT_ERROR}")
    if mode not in ("native", "fit"):
        raise CompositorError(f"unknown compositor mode {mode!r} (expected 'native' or 'fit')")

    canvas_w, canvas_h = canvas_size
    background = Image.open(background_path).convert("RGB")
    canvas = _resize_cover(background, canvas_w, canvas_h)

    product = Image.open(product_path).convert("RGBA")
    pw, ph = product.size

    if mode == "native":
        if pw > canvas_w or ph > canvas_h:
            raise CompositorError(
                f"product {pw}x{ph} does not fit canvas {canvas_w}x{canvas_h} in "
                "mode='native' (no resampling is allowed in this mode — pixel-exact "
                "preservation would be broken by downscaling). Use mode='fit' instead."
            )
        pasted, exact = product, True
    else:  # mode == "fit"
        max_w = max(1, int(canvas_w * (1 - 2 * margin)))
        max_h = max(1, int(canvas_h * (1 - 2 * margin)))
        scale = min(max_w / pw, max_h / ph, 1.0)  # never upscale
        if scale < 1.0:
            new_size = (max(1, round(pw * scale)), max(1, round(ph * scale)))
            pasted, exact = product.resize(new_size, Image.LANCZOS), False
        else:
            pasted, exact = product, True

    paste_w, paste_h = pasted.size
    x, y = _position_box(position, canvas_w, canvas_h, paste_w, paste_h)
    canvas.paste(pasted, (x, y), pasted)  # pasted as its own alpha mask

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, "PNG")

    return {
        "canvas_size": [canvas_w, canvas_h],
        "product_pixels_exact": exact,
        "product_box": [x, y, x + paste_w, y + paste_h],
        "mode": mode,
        "position": position,
    }


class LocalCompositorBackend(CreativeBackend):
    """Real, credential-free backend. See module docstring."""

    name = "local_compositor"

    def capabilities(self) -> frozenset[Capability]:
        return frozenset({Capability.IMAGE_EDIT})

    def check_credentials(self) -> CredentialStatus:
        if not _PIL_AVAILABLE:
            return CredentialStatus(
                False, f"Pillow is required and not importable: {_PIL_IMPORT_ERROR}"
            )
        return CredentialStatus(True, "no credentials required — local deterministic compositor")

    def estimate_cost(self, request: GenerationRequest) -> CostEstimate | None:
        return CostEstimate(
            amount=0.0, unit="usd", source="backend_quote", note="local compute only, no vendor spend"
        )

    def generate(self, request: GenerationRequest) -> list[GenerationResult]:
        # Defense-in-depth, same pattern as every other backend.
        if request.product_lock is not None:
            ProductFidelityGuard.check(self, request)

        cred = self.check_credentials()
        if not cred.ok:
            raise RuntimeError(f"local_compositor backend: {cred.detail}")
        if request.kind != "image_edit":
            raise ValueError(
                "local_compositor only supports kind='image_edit' "
                "(compositing a product source onto a background)"
            )
        if request.source is None:
            raise ValueError("local_compositor requires request.source = canonical product image path")
        if request.background is None:
            raise ValueError("local_compositor requires request.background = local background image path")
        if not request.source.exists():
            raise FileNotFoundError(f"product image not found: {request.source}")
        if not request.background.exists():
            raise FileNotFoundError(f"background image not found: {request.background}")

        aspect_ratio = request.aspect_ratio or "1:1"
        canvas_size = CANVAS_SIZES.get(aspect_ratio)
        if canvas_size is None:
            raise ValueError(
                f"unsupported aspect ratio {aspect_ratio!r}. Supported: {sorted(CANVAS_SIZES)}"
            )

        mode = request.options.get("mode", "fit")
        position = request.options.get("position", "center")
        margin = float(request.options.get("margin", 0.08))

        request.output_dir.mkdir(parents=True, exist_ok=True)
        stem = request.source.stem

        results: list[GenerationResult] = []
        for variant in range(1, request.n + 1):
            out_path = (
                request.output_dir
                / f"{stem}-{aspect_ratio.replace(':', 'x')}-v{variant}.png"
            )
            info = composite(
                product_path=request.source,
                background_path=request.background,
                out_path=out_path,
                canvas_size=canvas_size,
                mode=mode,
                position=position,
                margin=margin,
            )
            provenance = {
                "operation": _MODEL_NAME,
                "generative": False,
                "canvas_size": info["canvas_size"],
                "aspect_ratio": aspect_ratio,
                "mode": info["mode"],
                "position": info["position"],
                "product_pixels_exact": info["product_pixels_exact"],
                "product_box": info["product_box"],
                "product_path": str(request.source),
                "product_sha256": sha256_of(request.source),
                "background_path": str(request.background),
                "background_sha256": sha256_of(request.background),
                "output_sha256": sha256_of(out_path),
            }
            results.append(
                GenerationResult(
                    variant=variant,
                    path=out_path,
                    backend=self.name,
                    model=_MODEL_NAME,
                    cost=self.estimate_cost(request),
                    provenance=provenance,
                )
            )
        return results
