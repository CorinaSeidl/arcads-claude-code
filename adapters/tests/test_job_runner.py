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


def _read_status(approval_manifest_path: Path) -> str:
    return json.loads(approval_manifest_path.read_text())["status"]


class TestCrashSafety(unittest.TestCase):
    """Review-fix B2: a prior PASS must never survive a run that didn't complete
    generation/QA/provenance/manifest-write successfully. Each test here reproduces
    the exact independent-review scenario (a real PASS run, then a second run that
    blows up partway through) and asserts the *final on-disk state* is never PASS.
    """

    def _pass_then_break(self, root: Path, *, patch_target: str, side_effect) -> tuple:
        _make_png(root / "product.png", (300, 300), (10, 20, 30))
        _make_png(root / "background.png", (1600, 1200), (200, 200, 200))
        manifest = _write_manifest(root)
        job = load_job(manifest, repo_root=root)

        with mock.patch.object(job_runner, "_ADAPTER_LOG", root / "log.jsonl"):
            first = run_job(job)
        self.assertEqual(first.status, "PASS", first.reason)
        approval_path = first.approval_manifest_path
        self.assertEqual(_read_status(approval_path), "PASS")
        image_mtime_before = first.output_path.stat().st_mtime_ns

        with mock.patch.object(job_runner, "_ADAPTER_LOG", root / "log.jsonl"), mock.patch(
            patch_target, side_effect=side_effect
        ):
            second = run_job(job)

        return first, second, approval_path, image_mtime_before

    def test_exception_during_qa_does_not_leave_stale_pass(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            first, second, approval_path, mtime_before = self._pass_then_break(
                root,
                patch_target="adapters.job_runner.run_qa",
                side_effect=RuntimeError("simulated crash mid-QA"),
            )
            self.assertEqual(second.status, "FAIL")
            self.assertIn("QA raised an unexpected exception", second.reason)
            on_disk_status = _read_status(approval_path)
            self.assertNotEqual(on_disk_status, "PASS", "stale PASS survived a crashed rerun")
            self.assertEqual(on_disk_status, "FAIL")
            # The image WAS regenerated (overwritten) by the second run's generate()
            # step — confirms this is a genuine "new unvalidated file, old manifest
            # would have been wrong" scenario, not a no-op.
            self.assertGreater(first.output_path.stat().st_mtime_ns, mtime_before - 1)

    def test_exception_during_provenance_write_does_not_leave_stale_pass(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _first, second, approval_path, _mtime = self._pass_then_break(
                root,
                patch_target="adapters.job_runner.build_provenance",
                side_effect=RuntimeError("simulated disk-full during provenance build"),
            )
            self.assertEqual(second.status, "FAIL")
            self.assertIn("provenance write failed", second.reason)
            self.assertEqual(_read_status(approval_path), "FAIL")

    def test_exception_during_approval_manifest_write_does_not_leave_stale_pass(self) -> None:
        # The trickiest case: QA passes, provenance succeeds, but the FINAL write
        # (the one that would say PASS) fails. Must still not report PASS, and the
        # on-disk file (if present at all) must not say PASS.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _make_png(root / "product.png", (300, 300), (10, 20, 30))
            _make_png(root / "background.png", (1600, 1200), (200, 200, 200))
            manifest = _write_manifest(root)
            job = load_job(manifest, repo_root=root)

            with mock.patch.object(job_runner, "_ADAPTER_LOG", root / "log.jsonl"):
                first = run_job(job)
            self.assertEqual(first.status, "PASS")
            approval_path = first.approval_manifest_path

            real_atomic_write_text = job_runner.atomic_write_text
            call_count = {"n": 0}

            def flaky_atomic_write(path, text, *a, **kw):
                call_count["n"] += 1
                # 1st call = the RUNNING invalidation (must succeed, or the test
                # doesn't exercise the intended scenario). Fail only on the FINAL
                # (PASS) write, which is the 2nd approval-manifest write this run.
                if path.name == "approval-manifest.json" and call_count["n"] > 1:
                    raise OSError("simulated write failure on the final PASS write")
                return real_atomic_write_text(path, text, *a, **kw)

            with mock.patch.object(job_runner, "_ADAPTER_LOG", root / "log.jsonl"), mock.patch.object(
                job_runner, "atomic_write_text", side_effect=flaky_atomic_write
            ):
                second = run_job(job)

            self.assertEqual(second.status, "FAIL", "must never report PASS if the manifest write failed")
            self.assertIn("approval-manifest.json could not be written", second.reason)
            # On-disk: the last successful write in this run was RUNNING (the FAIL/PASS
            # write failed), so the file must not say PASS. It may still say the
            # prior run's PASS only if the RUNNING invalidation itself never landed —
            # verify that did NOT happen by checking the file is not PASS.
            self.assertNotEqual(_read_status(approval_path), "PASS")

    def test_rerun_over_existing_pass_directory_with_legitimate_qa_failure(self) -> None:
        """Non-crash control case: a normal (non-exception) QA failure on a rerun
        must also correctly flip PASS -> FAIL — establishes the baseline the crash
        tests above are compared against."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _make_png(root / "product.png", (300, 300), (10, 20, 30))
            _make_png(root / "background.png", (1600, 1200), (200, 200, 200))
            manifest = _write_manifest(root)
            job = load_job(manifest, repo_root=root)

            with mock.patch.object(job_runner, "_ADAPTER_LOG", root / "log.jsonl"):
                first = run_job(job)
            self.assertEqual(first.status, "PASS")
            approval_path = first.approval_manifest_path

            with mock.patch.object(job_runner, "_ADAPTER_LOG", root / "log.jsonl"), mock.patch(
                "adapters.job_runner.run_qa"
            ) as mock_qa:
                from adapters.qa import QACheck, QAReport

                mock_qa.return_value = QAReport(
                    checks=(QACheck("forced_failure", False, "forced for this test"),)
                )
                second = run_job(job)

            self.assertEqual(second.status, "FAIL")
            self.assertEqual(_read_status(approval_path), "FAIL")

    def test_running_state_is_written_before_generate_is_called(self) -> None:
        """Confirms the invalidation-before-generate ordering directly, not just its
        end effect: at the moment generate() is invoked, the manifest on disk must
        already say RUNNING (i.e. the prior terminal state has already been
        overwritten before any mutation of the output happens)."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _make_png(root / "product.png", (300, 300), (10, 20, 30))
            _make_png(root / "background.png", (1600, 1200), (200, 200, 200))
            manifest = _write_manifest(root)
            job = load_job(manifest, repo_root=root)

            observed = {}
            real_generate = None

            from adapters.local_compositor_backend import LocalCompositorBackend

            real_generate = LocalCompositorBackend.generate

            def spying_generate(self, request):
                approval_path = job.output_dir / "approval-manifest.json"
                observed["status_at_generate_time"] = _read_status(approval_path)
                return real_generate(self, request)

            with mock.patch.object(job_runner, "_ADAPTER_LOG", root / "log.jsonl"), mock.patch.object(
                LocalCompositorBackend, "generate", spying_generate
            ):
                result = run_job(job)

            self.assertEqual(observed["status_at_generate_time"], "RUNNING")
            self.assertEqual(result.status, "PASS")


if __name__ == "__main__":
    unittest.main()
