from __future__ import annotations

import unittest
from pathlib import Path

from adapters.base import CostEstimate, GenerationRequest, ProductLock


class TestProductLock(unittest.TestCase):
    def test_rejects_unsupported_mode(self) -> None:
        with self.assertRaises(ValueError):
            ProductLock(canonical_image=Path("x.jpg"), mode="mask")  # type: ignore[arg-type]

    def test_default_mode(self) -> None:
        lock = ProductLock(canonical_image=Path("x.jpg"))
        self.assertEqual(lock.mode, "environment-only")


class TestGenerationRequest(unittest.TestCase):
    def test_image_edit_requires_source(self) -> None:
        with self.assertRaises(ValueError):
            GenerationRequest(kind="image_edit", prompt="p")

    def test_image_edit_with_source_ok(self) -> None:
        req = GenerationRequest(kind="image_edit", prompt="p", source=Path("x.jpg"))
        self.assertEqual(req.source, Path("x.jpg"))

    def test_video_with_product_lock_rejected(self) -> None:
        with self.assertRaises(ValueError):
            GenerationRequest(
                kind="video",
                prompt="p",
                product_lock=ProductLock(canonical_image=Path("x.jpg")),
            )

    def test_n_out_of_range(self) -> None:
        with self.assertRaises(ValueError):
            GenerationRequest(kind="image", prompt="p", aspect_ratio="1:1", n=0)
        with self.assertRaises(ValueError):
            GenerationRequest(kind="image", prompt="p", aspect_ratio="1:1", n=11)

    def test_valid_image_request(self) -> None:
        req = GenerationRequest(kind="image", prompt="p", aspect_ratio="1:1", n=3)
        self.assertEqual(req.n, 3)


class TestCostEstimate(unittest.TestCase):
    def test_str_includes_unit_and_note(self) -> None:
        est = CostEstimate(amount=1.5, unit="credits", source="historical_log", note="avg of 3")
        s = str(est)
        self.assertIn("credits", s)
        self.assertIn("avg of 3", s)

    def test_str_without_note(self) -> None:
        est = CostEstimate(amount=2.0, unit="usd", source="unknown")
        self.assertEqual(str(est), "~2 usd")


if __name__ == "__main__":
    unittest.main()
