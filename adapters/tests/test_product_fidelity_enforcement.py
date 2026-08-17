"""Regression tests: a ProductLock'd request must never reach a full-generation
(text-to-image redraw) path, in any backend, even if a caller forgets to run the
guard explicitly. These exercise the real generate() entry points (defense-in-depth),
not just the standalone ProductFidelityGuard.check() function tested in test_policy.py.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from adapters import arcads_backend
from adapters.arcads_backend import ArcadsBackend
from adapters.base import GenerationRequest, ProductFidelityViolation, ProductLock
from adapters.local_compositor_backend import LocalCompositorBackend
from adapters.openai_backend import OpenAIBackend
from adapters.runway_backend import RunwayBackend


class TestProductLockCannotFallBackToFullGeneration(unittest.TestCase):
    def test_arcads_refuses_full_generation_kind_with_product_lock(self) -> None:
        """kind='image' is Arcads' plain text-to-image path — the one that would
        redraw the product from a description instead of preserving its pixels.
        A ProductLock on such a request must be refused before any subprocess (and
        therefore any Arcads HTTP call) is attempted."""
        with tempfile.NamedTemporaryFile(suffix=".jpg") as tmp:
            product = Path(tmp.name)
            request = GenerationRequest(
                kind="image",  # NOT image_edit — the unsafe path
                prompt="a bottle of matcha on a marble counter",
                aspect_ratio="1:1",
                model="nano-banana-2",
                product_lock=ProductLock(canonical_image=product),
            )
            with mock.patch("adapters.arcads_backend.subprocess.run") as mock_run:
                with self.assertRaises(ProductFidelityViolation):
                    ArcadsBackend().generate(request)
                mock_run.assert_not_called()  # proves it never got close to a real call

    def test_arcads_refuses_even_with_valid_credentials_present(self) -> None:
        """Confirms this is a fidelity check, not just a missing-credentials error —
        the violation must fire even when check_credentials() would otherwise pass."""
        with tempfile.TemporaryDirectory() as d:
            fake_env = Path(d) / ".env"
            fake_env.write_text("ARCADS_BASIC_AUTH='Basic YWJjZGVmZ2g6'\n")
            product = Path(d) / "product.jpg"
            product.write_bytes(b"fake-jpeg-bytes")
            request = GenerationRequest(
                kind="image",
                prompt="redraw the product from scratch",
                aspect_ratio="1:1",
                model="gpt-image-2",
                product_lock=ProductLock(canonical_image=product),
            )
            with mock.patch.object(arcads_backend, "_ENV_FILE", fake_env), mock.patch(
                "adapters.arcads_backend.subprocess.run"
            ) as mock_run:
                self.assertTrue(ArcadsBackend().check_credentials().ok)  # sanity: creds ARE fine
                with self.assertRaises(ProductFidelityViolation):
                    ArcadsBackend().generate(request)
                mock_run.assert_not_called()

    def test_stub_backends_refuse_product_locked_full_generation_before_not_implemented(
        self,
    ) -> None:
        """Even an unwritten backend must fail closed on fidelity, not just on
        'not implemented' — so wiring a real vendor later inherits the protection
        by construction rather than by remembering to add it."""
        with tempfile.NamedTemporaryFile(suffix=".png") as tmp:
            product = Path(tmp.name)
            request = GenerationRequest(
                kind="image",
                prompt="p",
                product_lock=ProductLock(canonical_image=product),
            )
            for cls in (OpenAIBackend, RunwayBackend):
                with self.assertRaises(ProductFidelityViolation, msg=cls.__name__):
                    cls().generate(request)

    def test_local_compositor_is_the_safe_alternative_and_succeeds(self) -> None:
        """The other half of the guarantee: the fidelity-preserving path (image_edit
        via local_compositor) is NOT blocked — only the unsafe path is."""
        from PIL import Image

        with tempfile.TemporaryDirectory() as d:
            product = Path(d) / "product.png"
            background = Path(d) / "bg.png"
            Image.new("RGB", (200, 200), (1, 2, 3)).save(product, "PNG")
            Image.new("RGB", (1600, 1200), (9, 9, 9)).save(background, "PNG")
            request = GenerationRequest(
                kind="image_edit",
                prompt="composite",
                source=product,
                background=background,
                aspect_ratio="1:1",
                output_dir=Path(d) / "out",
                product_lock=ProductLock(canonical_image=product),
            )
            results = LocalCompositorBackend().generate(request)  # should not raise
            self.assertEqual(len(results), 1)


if __name__ == "__main__":
    unittest.main()
