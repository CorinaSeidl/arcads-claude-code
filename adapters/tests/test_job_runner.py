"""Integration tests for the full local production job runner (adapters/job_runner.py).

Includes the deliberate failing case required by Phase 3: a job manifest requesting
an unsafe/unsupported backend must be refused before any network attempt — proven by
mocking the subprocess boundary and asserting it is never called, the same pattern
used for the Phase 2 product-fidelity regression tests.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from adapters import job_runner
from adapters.base import Capability, CostEstimate, CreativeBackend, CredentialStatus, GenerationRequest
from adapters.job import BackgroundSpec, CreativeJob, ProductSpec, load_job
from adapters.job_runner import run_job


def _make_png(path: Path, size: tuple[int, int], color: tuple[int, int, int]) -> None:
    Image.new("RGB", size, color).save(path, "PNG")


def _write_manifest(root: Path, **overrides) -> Path:
    data = {
        "job_id": "soulcraft-maca-matcha-test",
        "channel": "instagram_story",
        "product": {"asset": "product.png", "fidelity": "environment-only"},
        "background": {"asset": "background.png"},
        "aspect_ratio": "9:16",
        "output_dir": "out",
        "provenance_seed": {"requested_by": "hello@soulcraftapotheca.com", "notes": "test"},
    }
    data.update(overrides)
    manifest = root / "job.json"
    manifest.write_text(json.dumps(data))
    return manifest


class TestFullJobPasses(unittest.TestCase):
    def test_end_to_end_pass_writes_full_approval_package(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _make_png(root / "product.png", (300, 300), (10, 20, 30))
            _make_png(root / "background.png", (1600, 1200), (200, 200, 200))
            manifest = _write_manifest(root)
            job = load_job(manifest, repo_root=root)

            fake_log = root / "adapter-calls.jsonl"
            with mock.patch.object(job_runner, "_ADAPTER_LOG", fake_log):
                result = run_job(job)

            self.assertEqual(result.status, "PASS", result.reason)
            self.assertEqual(result.backend, "local_compositor")
            self.assertTrue(result.output_path.exists())
            self.assertTrue(result.qa.passed)
            self.assertEqual(len(result.qa.checks), 6)

            self.assertTrue(result.provenance_path.exists())
            provenance = json.loads(result.provenance_path.read_text())
            self.assertEqual(provenance["job_id"], "soulcraft-maca-matcha-test")
            self.assertEqual(provenance["requested_by"], "hello@soulcraftapotheca.com")
            self.assertTrue(provenance["qa_passed"])

            self.assertTrue(result.approval_manifest_path.exists())
            approval = json.loads(result.approval_manifest_path.read_text())
            self.assertEqual(approval["status"], "PASS")
            self.assertEqual(approval["job_id"], "soulcraft-maca-matcha-test")
            self.assertEqual(approval["output_path"], str(result.output_path))
            self.assertIn("qa", approval)
            self.assertEqual(len(approval["qa"]["checks"]), 6)

            self.assertTrue(fake_log.exists())
            logged = json.loads(fake_log.read_text().splitlines()[-1])
            self.assertEqual(logged["job_id"], "soulcraft-maca-matcha-test")

    def test_rerunning_same_job_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _make_png(root / "product.png", (300, 300), (7, 8, 9))
            _make_png(root / "background.png", (1600, 1200), (100, 150, 200))
            manifest = _write_manifest(root)
            job = load_job(manifest, repo_root=root)

            with mock.patch.object(job_runner, "_ADAPTER_LOG", root / "log1.jsonl"):
                r1 = run_job(job)
            first_bytes = r1.output_path.read_bytes()

            with mock.patch.object(job_runner, "_ADAPTER_LOG", root / "log2.jsonl"):
                r2 = run_job(job)
            second_bytes = r2.output_path.read_bytes()

            self.assertEqual(r1.status, "PASS")
            self.assertEqual(r2.status, "PASS")
            self.assertEqual(first_bytes, second_bytes)


class TestInputValidationFailures(unittest.TestCase):
    def test_missing_product_asset_fails_with_approval_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _make_png(root / "background.png", (800, 600), (1, 1, 1))
            manifest = _write_manifest(root)  # product.png intentionally not created
            job = load_job(manifest, repo_root=root)
            result = run_job(job)
            self.assertEqual(result.status, "FAIL")
            self.assertIn("product asset not found", result.reason)
            self.assertIsNone(result.output_path)
            self.assertIsNone(result.provenance_path)
            self.assertIsNotNone(result.approval_manifest_path)
            approval = json.loads(result.approval_manifest_path.read_text())
            self.assertEqual(approval["status"], "FAIL")

    def test_missing_background_fails_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _make_png(root / "product.png", (300, 300), (1, 1, 1))
            manifest = _write_manifest(root, background=None)
            job = load_job(manifest, repo_root=root)
            result = run_job(job)
            self.assertEqual(result.status, "FAIL")
            self.assertIn("background", result.reason)

    def test_unsupported_aspect_ratio_fails_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _make_png(root / "product.png", (300, 300), (1, 1, 1))
            _make_png(root / "background.png", (800, 600), (1, 1, 1))
            manifest = _write_manifest(root, aspect_ratio="21:9")
            job = load_job(manifest, repo_root=root)
            result = run_job(job)
            self.assertEqual(result.status, "FAIL")
            self.assertIn("aspect_ratio", result.reason)


class TestDeliberateUnsafeBackendFailure(unittest.TestCase):
    """The Phase 3-required deliberate failing case: a manifest asking for an
    unsafe/unsupported backend must be refused, with zero network attempted."""

    def test_arcads_backend_is_refused_before_any_subprocess_call(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _make_png(root / "product.png", (300, 300), (1, 1, 1))
            _make_png(root / "background.png", (800, 600), (1, 1, 1))
            manifest = _write_manifest(root, backend="arcads")
            job = load_job(manifest, repo_root=root)

            with mock.patch("adapters.arcads_backend.subprocess.run") as mock_run:
                result = run_job(job)
                mock_run.assert_not_called()  # proves zero network was ever attempted

            self.assertEqual(result.status, "FAIL")
            self.assertIn("not enabled for run-job", result.reason)
            self.assertIsNone(result.output_path)
            self.assertIsNone(result.provenance_path)
            self.assertIsNotNone(result.approval_manifest_path)  # still documented, not silently dropped
            approval = json.loads(result.approval_manifest_path.read_text())
            self.assertEqual(approval["status"], "FAIL")
            self.assertIn("not enabled for run-job", approval["reason"])

    def test_unknown_backend_name_is_also_refused(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _make_png(root / "product.png", (300, 300), (1, 1, 1))
            _make_png(root / "background.png", (800, 600), (1, 1, 1))
            manifest = _write_manifest(root, backend="openai")
            job = load_job(manifest, repo_root=root)
            result = run_job(job)
            self.assertEqual(result.status, "FAIL")
            self.assertIn("not enabled for run-job", result.reason)


class _FakeFullGenBackend(CreativeBackend):
    """A hypothetical future backend with no IMAGE_EDIT capability — used only to
    prove the ProductFidelityGuard itself (not just the backend allowlist) still
    protects run_job, in case a later phase widens SUPPORTED_BACKENDS_FOR_RUN_JOB."""

    name = "fake_full_gen"

    def capabilities(self) -> frozenset[Capability]:
        return frozenset({Capability.IMAGE})  # no IMAGE_EDIT — cannot preserve product pixels

    def check_credentials(self) -> CredentialStatus:
        return CredentialStatus(True, "ok")

    def estimate_cost(self, request: GenerationRequest):
        return CostEstimate(amount=0.0, unit="usd", source="unknown")

    def generate(self, request: GenerationRequest):
        raise AssertionError("generate() must never be reached — the guard should stop this first")


class TestGuardStillProtectsBeyondTheAllowlist(unittest.TestCase):
    def test_product_fidelity_guard_blocks_a_hypothetical_future_unsafe_backend(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _make_png(root / "product.png", (300, 300), (1, 1, 1))
            _make_png(root / "background.png", (800, 600), (1, 1, 1))
            manifest = _write_manifest(root, backend="fake_full_gen")
            job = load_job(manifest, repo_root=root)

            with mock.patch.object(
                job_runner, "SUPPORTED_BACKENDS_FOR_RUN_JOB", frozenset({"fake_full_gen"})
            ), mock.patch.object(job_runner, "get_backend", return_value=_FakeFullGenBackend()):
                result = run_job(job)  # _FakeFullGenBackend.generate() would raise if reached

            self.assertEqual(result.status, "FAIL")
            self.assertIn("product-fidelity violation", result.reason)


if __name__ == "__main__":
    unittest.main()
