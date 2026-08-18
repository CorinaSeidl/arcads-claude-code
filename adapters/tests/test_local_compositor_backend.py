from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from adapters.base import Capability, GenerationRequest, ProductLock
from adapters.local_compositor_backend import CANVAS_SIZES, LocalCompositorBackend, composite


def _make_png(path: Path, size: tuple[int, int], color: tuple[int, int, int]) -> None:
    Image.new("RGB", size, color).save(path, "PNG")


class TestCapabilitiesAndCredentials(unittest.TestCase):
    def test_capabilities_is_image_edit_only(self) -> None:
        backend = LocalCompositorBackend()
        self.assertEqual(backend.capabilities(), frozenset({Capability.IMAGE_EDIT}))

    def test_credentials_are_never_required(self) -> None:
        status = LocalCompositorBackend().check_credentials()
        self.assertTrue(status.ok)
        self.assertIn("no credentials required", status.detail)

    def test_cost_is_always_zero_usd(self) -> None:
        req = GenerationRequest(kind="image_edit", prompt="p", source=Path("x.png"))
        estimate = LocalCompositorBackend().estimate_cost(req)
        self.assertEqual(estimate.amount, 0.0)
        self.assertEqual(estimate.unit, "usd")


class TestCompositeAspectRatios(unittest.TestCase):
    def test_all_standard_aspect_ratios_produce_correct_canvas(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            product = Path(d) / "product.png"
            background = Path(d) / "bg.png"
            _make_png(product, (200, 200), (255, 0, 0))
            _make_png(background, (800, 600), (0, 255, 0))

            for ratio, (w, h) in CANVAS_SIZES.items():
                out = Path(d) / f"out-{ratio.replace(':', 'x')}.png"
                info = composite(product, background, out, (w, h), mode="fit")
                with Image.open(out) as img:
                    self.assertEqual(img.size, (w, h), msg=ratio)
                self.assertEqual(info["canvas_size"], [w, h])

    def test_unsupported_aspect_ratio_rejected_by_backend(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            product = Path(d) / "product.png"
            background = Path(d) / "bg.png"
            _make_png(product, (200, 200), (255, 0, 0))
            _make_png(background, (800, 600), (0, 255, 0))
            req = GenerationRequest(
                kind="image_edit",
                prompt="p",
                source=product,
                background=background,
                aspect_ratio="21:9",  # not a supported social preset
                output_dir=Path(d) / "out",
            )
            with self.assertRaises(ValueError):
                LocalCompositorBackend().generate(req)


class TestProductPixelPreservation(unittest.TestCase):
    def test_native_mode_is_byte_exact_for_a_product_that_fits(self) -> None:
        """The core claim: when the product fits without resampling, every pixel
        placed on the canvas is byte-identical to the source product image."""
        with tempfile.TemporaryDirectory() as d:
            product = Path(d) / "product.png"
            background = Path(d) / "bg.png"
            # Small enough to fit natively inside a 1080x1080 canvas.
            _make_png(product, (300, 200), (10, 20, 30))
            _make_png(background, (1600, 1200), (200, 200, 200))
            out = Path(d) / "out.png"

            info = composite(product, background, out, (1080, 1080), mode="native")
            self.assertTrue(info["product_pixels_exact"])

            with Image.open(product) as src, Image.open(out) as result:
                x0, y0, x1, y1 = info["product_box"]
                cropped = result.convert("RGB").crop((x0, y0, x1, y1))
                self.assertEqual(list(cropped.getdata()), list(src.convert("RGB").getdata()))

    def test_native_mode_refuses_to_silently_downscale_oversized_product(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            product = Path(d) / "product.png"
            background = Path(d) / "bg.png"
            _make_png(product, (2000, 2000), (10, 20, 30))  # too big for a 1080x1080 canvas
            _make_png(background, (1600, 1200), (200, 200, 200))
            out = Path(d) / "out.png"
            with self.assertRaises(Exception):
                composite(product, background, out, (1080, 1080), mode="native")

    def test_fit_mode_flags_non_exact_when_resizing_occurs(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            product = Path(d) / "product.png"
            background = Path(d) / "bg.png"
            _make_png(product, (2000, 2000), (10, 20, 30))
            _make_png(background, (1600, 1200), (200, 200, 200))
            out = Path(d) / "out.png"
            info = composite(product, background, out, (1080, 1080), mode="fit")
            self.assertFalse(info["product_pixels_exact"])

    def test_fit_mode_is_exact_when_product_already_small_enough(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            product = Path(d) / "product.png"
            background = Path(d) / "bg.png"
            _make_png(product, (100, 100), (10, 20, 30))  # well within the 84% budget
            _make_png(background, (1600, 1200), (200, 200, 200))
            out = Path(d) / "out.png"
            info = composite(product, background, out, (1080, 1080), mode="fit")
            self.assertTrue(info["product_pixels_exact"])


class TestDeterministicRepeatability(unittest.TestCase):
    def test_same_inputs_produce_byte_identical_output(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            product = Path(d) / "product.png"
            background = Path(d) / "bg.png"
            _make_png(product, (400, 300), (5, 6, 7))
            _make_png(background, (1600, 1200), (222, 111, 33))
            out1 = Path(d) / "out1.png"
            out2 = Path(d) / "out2.png"

            composite(product, background, out1, (1080, 1350), mode="fit")
            composite(product, background, out2, (1080, 1350), mode="fit")

            self.assertEqual(out1.read_bytes(), out2.read_bytes())


class TestGenerateEndToEnd(unittest.TestCase):
    def test_generate_writes_file_and_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            product = Path(d) / "product.png"
            background = Path(d) / "bg.png"
            _make_png(product, (300, 300), (1, 2, 3))
            _make_png(background, (1600, 1200), (100, 150, 200))
            out_dir = Path(d) / "generated"

            req = GenerationRequest(
                kind="image_edit",
                prompt="composite",
                source=product,
                background=background,
                aspect_ratio="9:16",
                output_dir=out_dir,
                product_lock=ProductLock(canonical_image=product),
            )
            results = LocalCompositorBackend().generate(req)
            self.assertEqual(len(results), 1)
            result = results[0]
            self.assertTrue(result.path.exists())
            self.assertEqual(result.backend, "local_compositor")
            self.assertEqual(result.cost.amount, 0.0)
            self.assertIn("product_sha256", result.provenance)
            self.assertIn("background_sha256", result.provenance)
            self.assertTrue(result.provenance["product_pixels_exact"] in (True, False))
            self.assertFalse(result.provenance["generative"])

    def test_missing_background_raises_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            product = Path(d) / "product.png"
            _make_png(product, (100, 100), (1, 2, 3))
            req = GenerationRequest(
                kind="image_edit",
                prompt="composite",
                source=product,
                background=Path(d) / "does-not-exist.png",
                aspect_ratio="1:1",
                output_dir=Path(d) / "out",
            )
            with self.assertRaises(FileNotFoundError):
                LocalCompositorBackend().generate(req)


if __name__ == "__main__":
    unittest.main()
