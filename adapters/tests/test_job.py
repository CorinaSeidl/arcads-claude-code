from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from adapters.job import CreativeJob, JobValidationError, load_job


def _write(d: Path, name: str, data: dict) -> Path:
    p = d / name
    p.write_text(json.dumps(data))
    return p


def _minimal_manifest(**overrides) -> dict:
    data = {
        "job_id": "test-job",
        "channel": "instagram_story",
        "product": {"asset": "references/products/x.jpg", "fidelity": "environment-only"},
        "background": {"asset": "outputs/demo/bg.png"},
        "aspect_ratio": "9:16",
        "output_dir": "outputs/jobs/test-job",
    }
    data.update(overrides)
    return data


class TestLoadJobHappyPath(unittest.TestCase):
    def test_loads_full_manifest_with_repo_root_relative_paths(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            manifest = _write(root, "job.json", _minimal_manifest())
            job = load_job(manifest, repo_root=root)
            self.assertIsInstance(job, CreativeJob)
            self.assertEqual(job.job_id, "test-job")
            self.assertEqual(job.channel, "instagram_story")
            self.assertEqual(job.product.asset, root / "references/products/x.jpg")
            self.assertEqual(job.product.fidelity, "environment-only")
            self.assertEqual(job.background.asset, root / "outputs/demo/bg.png")
            self.assertEqual(job.aspect_ratio, "9:16")
            self.assertEqual(job.output_dir, root / "outputs/jobs/test-job")
            self.assertEqual(job.backend, "local_compositor")  # default
            self.assertEqual(job.composite.mode, "fit")  # default

    def test_absolute_asset_paths_are_not_re_rooted(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            abs_product = (root / "elsewhere" / "product.jpg")
            manifest = _write(
                root,
                "job.json",
                _minimal_manifest(product={"asset": str(abs_product), "fidelity": "none"}),
            )
            job = load_job(manifest, repo_root=root)
            self.assertEqual(job.product.asset, abs_product)

    def test_defaults_applied_when_optional_fields_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            data = _minimal_manifest()
            del data["background"]
            manifest = _write(root, "job.json", data)
            job = load_job(manifest, repo_root=root)
            self.assertIsNone(job.background)
            self.assertEqual(job.provenance_seed.requested_by, "")
            self.assertEqual(job.provenance_seed.notes, "")

    def test_composite_and_provenance_seed_overrides_applied(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            manifest = _write(
                root,
                "job.json",
                _minimal_manifest(
                    composite={"mode": "native", "position": "bottom", "margin": 0.1},
                    provenance_seed={"requested_by": "hello@soulcraftapotheca.com", "notes": "x"},
                ),
            )
            job = load_job(manifest, repo_root=root)
            self.assertEqual(job.composite.mode, "native")
            self.assertEqual(job.composite.position, "bottom")
            self.assertEqual(job.composite.margin, 0.1)
            self.assertEqual(job.provenance_seed.requested_by, "hello@soulcraftapotheca.com")


class TestLoadJobValidation(unittest.TestCase):
    def test_missing_file_raises(self) -> None:
        with self.assertRaises(JobValidationError):
            load_job(Path("/nonexistent/job.json"))

    def test_invalid_json_raises(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            bad = root / "job.json"
            bad.write_text("{not json")
            with self.assertRaises(JobValidationError):
                load_job(bad, repo_root=root)

    def test_missing_required_field_raises(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            data = _minimal_manifest()
            del data["job_id"]
            manifest = _write(root, "job.json", data)
            with self.assertRaises(JobValidationError):
                load_job(manifest, repo_root=root)

    def test_missing_product_asset_field_raises(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            manifest = _write(root, "job.json", _minimal_manifest(product={"fidelity": "none"}))
            with self.assertRaises(JobValidationError):
                load_job(manifest, repo_root=root)

    def test_invalid_fidelity_value_raises(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            manifest = _write(
                root,
                "job.json",
                _minimal_manifest(product={"asset": "x.jpg", "fidelity": "full-regeneration-ok"}),
            )
            with self.assertRaises(JobValidationError):
                load_job(manifest, repo_root=root)


class TestCreativeJobConversion(unittest.TestCase):
    def test_to_generation_request_sets_product_lock_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            manifest = _write(root, "job.json", _minimal_manifest())
            job = load_job(manifest, repo_root=root)
            request = job.to_generation_request()
            self.assertEqual(request.kind, "image_edit")
            self.assertIsNotNone(request.product_lock)
            self.assertEqual(request.product_lock.canonical_image, job.product.asset)
            self.assertEqual(request.source, job.product.asset)
            self.assertEqual(request.background, job.background.asset)
            self.assertEqual(request.aspect_ratio, "9:16")
            self.assertEqual(request.options["mode"], "fit")

    def test_fidelity_none_produces_no_product_lock(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            manifest = _write(
                root, "job.json", _minimal_manifest(product={"asset": "x.jpg", "fidelity": "none"})
            )
            job = load_job(manifest, repo_root=root)
            request = job.to_generation_request()
            self.assertIsNone(request.product_lock)


if __name__ == "__main__":
    unittest.main()
