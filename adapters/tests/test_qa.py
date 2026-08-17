from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from adapters import qa
from adapters.job import BackgroundSpec, CreativeJob, ProductSpec
from adapters.local_compositor_backend import LocalCompositorBackend
from adapters.policy import sha256_of


def _make_png(path: Path, size: tuple[int, int], color: tuple[int, int, int]) -> None:
    Image.new("RGB", size, color).save(path, "PNG")


def _real_run(d: Path) -> tuple[CreativeJob, object, object, str]:
    """Produce a real, valid job/request/result triple via the actual backend, so QA
    checks are exercised against genuine output, not a hand-built fixture."""
    product = d / "product.png"
    background = d / "background.png"
    _make_png(product, (200, 200), (10, 20, 30))
    _make_png(background, (1600, 1200), (200, 200, 200))

    job = CreativeJob(
        job_id="qa-test-job",
        channel="test",
        product=ProductSpec(asset=product, fidelity="environment-only"),
        aspect_ratio="1:1",
        output_dir=d / "out",
        background=BackgroundSpec(asset=background),
    )
    request = job.to_generation_request()
    pre_hash = sha256_of(product)
    results = LocalCompositorBackend().generate(request)
    return job, request, results[0], pre_hash


class TestOutputExistsAndOpens(unittest.TestCase):
    def test_passes_for_real_output(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            _, _, result, _ = _real_run(Path(d))
            check = qa._check_output_exists_and_opens(result)
            self.assertTrue(check.passed)

    def test_fails_for_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            _, _, result, _ = _real_run(Path(d))
            result.path.unlink()
            check = qa._check_output_exists_and_opens(result)
            self.assertFalse(check.passed)


class TestDimensions(unittest.TestCase):
    def test_passes_for_correct_canvas(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            job, _, result, _ = _real_run(Path(d))
            check = qa._check_dimensions(result, job.aspect_ratio)
            self.assertTrue(check.passed)

    def test_fails_when_aspect_ratio_mismatches_actual_file(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            _, _, result, _ = _real_run(Path(d))
            check = qa._check_dimensions(result, "16:9")  # file is actually 1:1 (1080x1080)
            self.assertFalse(check.passed)


class TestProductNotMutated(unittest.TestCase):
    def test_passes_when_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            job, _, _, pre_hash = _real_run(Path(d))
            check = qa._check_product_not_mutated(job, pre_hash)
            self.assertTrue(check.passed)

    def test_fails_when_product_file_was_modified(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            job, _, _, pre_hash = _real_run(Path(d))
            _make_png(job.product.asset, (50, 50), (255, 0, 0))  # simulate mutation
            check = qa._check_product_not_mutated(job, pre_hash)
            self.assertFalse(check.passed)

    def test_fails_when_no_pre_run_hash_captured(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            job, _, _, _ = _real_run(Path(d))
            check = qa._check_product_not_mutated(job, None)
            self.assertFalse(check.passed)


class TestProvenanceHashesResolve(unittest.TestCase):
    def test_passes_for_real_result(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            _, _, result, _ = _real_run(Path(d))
            check = qa._check_provenance_hashes_resolve(result)
            self.assertTrue(check.passed)

    def test_fails_when_provenance_hash_was_tampered(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            _, _, result, _ = _real_run(Path(d))
            tampered = dict(result.provenance)
            tampered["output_sha256"] = "0" * 64
            import dataclasses

            bad_result = dataclasses.replace(result, provenance=tampered)
            check = qa._check_provenance_hashes_resolve(bad_result)
            self.assertFalse(check.passed)


class TestNoUnexpectedOverlay(unittest.TestCase):
    def test_passes_for_genuine_compositor_output(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            job, request, result, _ = _real_run(Path(d))
            check = qa._check_no_unexpected_overlay(job, request, result)
            self.assertTrue(check.passed, check.detail)

    def test_fails_when_something_was_drawn_outside_the_product_box(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            job, request, result, _ = _real_run(Path(d))
            # Simulate an "unexpected overlay" — e.g. a stamped watermark/caption a
            # buggy pipeline step might add — drawn well outside the product's box.
            with Image.open(result.path) as img:
                img = img.convert("RGB")
                draw = ImageDraw.Draw(img)
                draw.rectangle([0, 0, 40, 40], fill=(255, 0, 255))
                img.save(result.path, "PNG")
            check = qa._check_no_unexpected_overlay(job, request, result)
            self.assertFalse(check.passed)


class TestDeterministicRerun(unittest.TestCase):
    def test_passes_for_untouched_output(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            job, request, result, _ = _real_run(Path(d))
            check = qa._check_deterministic_rerun(job, request, result)
            self.assertTrue(check.passed)

    def test_fails_when_output_was_altered_after_generation(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            job, request, result, _ = _real_run(Path(d))
            _make_png(result.path, (1080, 1080), (1, 1, 1))  # overwrite with something else
            check = qa._check_deterministic_rerun(job, request, result)
            self.assertFalse(check.passed)


class TestRunQAAggregate(unittest.TestCase):
    def test_all_checks_pass_for_a_clean_real_run(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            job, request, result, pre_hash = _real_run(Path(d))
            report = qa.run_qa(job, request, result, pre_hash)
            self.assertTrue(report.passed, report.as_dict())
            self.assertEqual(len(report.checks), 6)

    def test_report_fails_overall_if_any_single_check_fails(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            job, request, result, _ = _real_run(Path(d))
            report = qa.run_qa(job, request, result, None)  # no pre-hash -> that check fails
            self.assertFalse(report.passed)
            names = {c.name: c.passed for c in report.checks}
            self.assertFalse(names["product_asset_not_mutated"])


if __name__ == "__main__":
    unittest.main()
