from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from adapters import arcads_backend
from adapters.arcads_backend import ArcadsBackend, _script_for_model
from adapters.base import GenerationRequest


class TestScriptRouting(unittest.TestCase):
    def test_gpt_image_routes_to_chatgpt_script(self) -> None:
        self.assertEqual(_script_for_model("gpt-image-2"), arcads_backend._CHATGPT_SCRIPT)

    def test_nano_banana_routes_to_nano_banana_script(self) -> None:
        self.assertEqual(
            _script_for_model("nano-banana-pro"), arcads_backend._NANO_BANANA_SCRIPT
        )

    def test_unknown_model_raises(self) -> None:
        with self.assertRaises(ValueError):
            _script_for_model("seedance-2.0")  # video model — not wired in phase 1


class TestCapabilities(unittest.TestCase):
    def test_image_family_only(self) -> None:
        backend = ArcadsBackend()
        caps = {c.value for c in backend.capabilities()}
        self.assertEqual(caps, {"image", "image_edit"})


class TestCheckCredentials(unittest.TestCase):
    def test_missing_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            fake_env = Path(d) / ".env"  # does not exist
            with mock.patch.object(arcads_backend, "_ENV_FILE", fake_env):
                status = ArcadsBackend().check_credentials()
                self.assertFalse(status.ok)
                self.assertIn(".env not found", status.detail)

    def test_placeholder_only(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            fake_env = Path(d) / ".env"
            fake_env.write_text("ARCADS_BASIC_AUTH='Basic your_base64_encoded_credentials_here'\n")
            with mock.patch.object(arcads_backend, "_ENV_FILE", fake_env):
                status = ArcadsBackend().check_credentials()
                self.assertFalse(status.ok)

    def test_real_looking_basic_auth(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            fake_env = Path(d) / ".env"
            fake_env.write_text("ARCADS_BASIC_AUTH='Basic YWJjZGVmZ2g6'\n")
            with mock.patch.object(arcads_backend, "_ENV_FILE", fake_env):
                status = ArcadsBackend().check_credentials()
                self.assertTrue(status.ok)


class TestGenerateFailsClosedWithoutCredentials(unittest.TestCase):
    def test_generate_raises_before_any_subprocess_call(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            fake_env = Path(d) / ".env"  # missing on purpose
            request = GenerationRequest(
                kind="image", prompt="p", aspect_ratio="1:1", model="nano-banana-2"
            )
            with mock.patch.object(arcads_backend, "_ENV_FILE", fake_env), mock.patch(
                "adapters.arcads_backend.subprocess.run"
            ) as mock_run:
                with self.assertRaises(RuntimeError):
                    ArcadsBackend().generate(request)
                mock_run.assert_not_called()

    def test_generate_requires_model(self) -> None:
        request = GenerationRequest(kind="image", prompt="p", aspect_ratio="1:1")
        with self.assertRaises(ValueError):
            ArcadsBackend().generate(request)

    def test_generate_rejects_video_kind(self) -> None:
        request = GenerationRequest(
            kind="video", prompt="p", model="seedance-2.0"
        )
        with self.assertRaises(ValueError):
            ArcadsBackend().generate(request)


class TestEstimateCostOffline(unittest.TestCase):
    def test_no_log_file_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            fake_log = Path(d) / "arcads-api.jsonl"  # does not exist
            request = GenerationRequest(
                kind="image", prompt="p", aspect_ratio="1:1", model="nano-banana-2"
            )
            with mock.patch.object(arcads_backend, "_ARCADS_LOG", fake_log):
                self.assertIsNone(ArcadsBackend().estimate_cost(request))

    def test_averages_matching_model_entries(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            fake_log = Path(d) / "arcads-api.jsonl"
            fake_log.write_text(
                '{"model": "nano-banana-2", "response": {"status": "generated", "creditsCharged": 0.03}}\n'
                '{"model": "nano-banana-2", "response": {"status": "generated", "creditsCharged": 0.05}}\n'
                '{"model": "gpt-image-2", "response": {"status": "generated", "creditsCharged": 99}}\n'
                '{"model": "nano-banana-2", "response": {"status": "failed"}}\n'
            )
            request = GenerationRequest(
                kind="image", prompt="p", aspect_ratio="1:1", model="nano-banana-2"
            )
            with mock.patch.object(arcads_backend, "_ARCADS_LOG", fake_log):
                estimate = ArcadsBackend().estimate_cost(request)
                self.assertIsNotNone(estimate)
                self.assertAlmostEqual(estimate.amount, 0.04)
                self.assertEqual(estimate.unit, "credits")
                self.assertEqual(estimate.source, "historical_log")


def _valid_env(d: str) -> Path:
    fake_env = Path(d) / ".env"
    fake_env.write_text("ARCADS_BASIC_AUTH='Basic YWJjZGVmZ2g6'\n")
    return fake_env


class TestGenerateHappyPathMocked(unittest.TestCase):
    """Review-fix N4: ArcadsBackend.generate()'s actual execution logic — argv
    construction, subprocess.run invocation, stdout parsing — had zero coverage
    beyond guard-rejection paths. These mock subprocess.run itself (never a real
    Arcads call, never real network) and assert the request/response contract.
    """

    def test_image_kind_builds_expected_argv_and_parses_results(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            fake_env = _valid_env(d)
            out_dir = Path(d) / "generated"
            stdout = json.dumps({
                "variant": 1, "path": str(out_dir / "img-v1.png"), "asset_id": "abc-123",
                "width": 1024, "height": 1024, "prompt": "a nice ad", "mode": "image",
                "aspect_ratio": "1:1", "model": "gpt-image-2",
            }) + "\n"
            fake_proc = subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")
            request = GenerationRequest(
                kind="image", prompt="a nice ad", aspect_ratio="1:1",
                model="gpt-image-2", output_dir=out_dir, n=1,
            )
            with mock.patch.object(arcads_backend, "_ENV_FILE", fake_env), mock.patch(
                "adapters.arcads_backend.subprocess.run", return_value=fake_proc
            ) as mock_run:
                results = ArcadsBackend().generate(request)

            self.assertEqual(len(results), 1)
            result = results[0]
            self.assertEqual(result.variant, 1)
            self.assertEqual(result.path, Path(str(out_dir / "img-v1.png")))
            self.assertEqual(result.backend, "arcads")
            self.assertEqual(result.model, "gpt-image-2")
            self.assertIsNone(result.cost)  # Arcads has no billing endpoint (documented)
            self.assertEqual(result.provenance["arcads_asset_id"], "abc-123")
            self.assertEqual(result.provenance["mode"], "image")
            self.assertEqual(result.provenance["aspect_ratio"], "1:1")

            mock_run.assert_called_once()
            argv = mock_run.call_args.args[0]
            self.assertEqual(argv[0], sys.executable)
            self.assertEqual(argv[1], str(arcads_backend._CHATGPT_SCRIPT))

            def flag(name: str) -> str:
                return argv[argv.index(name) + 1]

            self.assertEqual(flag("--prompt"), "a nice ad")
            self.assertEqual(flag("--mode"), "image")
            self.assertEqual(flag("--aspect-ratio"), "1:1")
            self.assertEqual(flag("--n"), "1")
            self.assertEqual(flag("--out"), str(out_dir))
            self.assertEqual(flag("--model"), "gpt-image-2")
            self.assertEqual(flag("--env-file"), str(fake_env))
            self.assertNotIn("--source", argv)  # image kind, not image_edit
            # capture_output/text/check must match generate_image.py's stdout/stderr contract
            self.assertEqual(mock_run.call_args.kwargs.get("capture_output"), True)
            self.assertEqual(mock_run.call_args.kwargs.get("text"), True)

    def test_image_edit_kind_passes_source_not_aspect_ratio(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            fake_env = _valid_env(d)
            source = Path(d) / "existing.png"
            source.write_bytes(b"x")
            out_dir = Path(d) / "generated"
            stdout = json.dumps({
                "variant": 1, "path": str(out_dir / "edited-v1.png"), "asset_id": "edit-1",
                "width": 512, "height": 512, "prompt": "edit it", "mode": "image_edit",
                "aspect_ratio": None, "model": "nano-banana-edit",
            }) + "\n"
            fake_proc = subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")
            request = GenerationRequest(
                kind="image_edit", prompt="edit it", source=source,
                model="nano-banana-edit", output_dir=out_dir,
            )
            with mock.patch.object(arcads_backend, "_ENV_FILE", fake_env), mock.patch(
                "adapters.arcads_backend.subprocess.run", return_value=fake_proc
            ) as mock_run:
                results = ArcadsBackend().generate(request)

            self.assertEqual(len(results), 1)
            argv = mock_run.call_args.args[0]
            self.assertIn("--source", argv)
            self.assertEqual(argv[argv.index("--source") + 1], str(source))
            self.assertNotIn("--aspect-ratio", argv)
            # nano-banana* routes to the nano-banana-image-ad script, not chatgpt's
            self.assertEqual(argv[1], str(arcads_backend._NANO_BANANA_SCRIPT))

    def test_multiple_variants_and_references_are_all_parsed_and_passed(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            fake_env = _valid_env(d)
            ref1, ref2 = Path(d) / "ref1.png", Path(d) / "ref2.png"
            ref1.write_bytes(b"1")
            ref2.write_bytes(b"2")
            out_dir = Path(d) / "generated"
            stdout = "\n".join(
                json.dumps({
                    "variant": i, "path": str(out_dir / f"img-v{i}.png"), "asset_id": f"asset-{i}",
                    "width": 1024, "height": 1024, "prompt": "p", "mode": "image",
                    "aspect_ratio": "1:1", "model": "gpt-image-2",
                })
                for i in (1, 2)
            ) + "\n"
            fake_proc = subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")
            request = GenerationRequest(
                kind="image", prompt="p", aspect_ratio="1:1", model="gpt-image-2",
                output_dir=out_dir, n=2, references=(ref1, ref2),
            )
            with mock.patch.object(arcads_backend, "_ENV_FILE", fake_env), mock.patch(
                "adapters.arcads_backend.subprocess.run", return_value=fake_proc
            ) as mock_run:
                results = ArcadsBackend().generate(request)

            self.assertEqual([r.variant for r in results], [1, 2])
            argv = mock_run.call_args.args[0]
            self.assertEqual(argv.count("--image-ref"), 2)
            self.assertIn(str(ref1), argv)
            self.assertIn(str(ref2), argv)

    def test_returncode_2_raises_value_error_with_stderr(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            fake_env = _valid_env(d)
            fake_proc = subprocess.CompletedProcess(
                args=[], returncode=2, stdout="", stderr="error: bad arguments\n"
            )
            request = GenerationRequest(
                kind="image", prompt="p", aspect_ratio="1:1", model="gpt-image-2",
                output_dir=Path(d) / "generated",
            )
            with mock.patch.object(arcads_backend, "_ENV_FILE", fake_env), mock.patch(
                "adapters.arcads_backend.subprocess.run", return_value=fake_proc
            ):
                with self.assertRaises(ValueError) as ctx:
                    ArcadsBackend().generate(request)
            self.assertIn("bad arguments", str(ctx.exception))

    def test_returncode_1_with_empty_stdout_raises_runtime_error(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            fake_env = _valid_env(d)
            fake_proc = subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="error: all variants failed\n"
            )
            request = GenerationRequest(
                kind="image", prompt="p", aspect_ratio="1:1", model="gpt-image-2",
                output_dir=Path(d) / "generated",
            )
            with mock.patch.object(arcads_backend, "_ENV_FILE", fake_env), mock.patch(
                "adapters.arcads_backend.subprocess.run", return_value=fake_proc
            ):
                with self.assertRaises(RuntimeError) as ctx:
                    ArcadsBackend().generate(request)
            self.assertIn("all variants failed", str(ctx.exception))

    def test_returncode_1_with_partial_stdout_still_returns_successful_variants(self) -> None:
        # Mirrors generate_image.py's own contract: exit 1 with non-empty stdout means
        # partial success (some variants failed, some succeeded), not total failure.
        with tempfile.TemporaryDirectory() as d:
            fake_env = _valid_env(d)
            out_dir = Path(d) / "generated"
            stdout = json.dumps({
                "variant": 1, "path": str(out_dir / "img-v1.png"), "asset_id": "ok-1",
                "width": 1024, "height": 1024, "prompt": "p", "mode": "image",
                "aspect_ratio": "1:1", "model": "gpt-image-2",
            }) + "\n"
            fake_proc = subprocess.CompletedProcess(
                args=[], returncode=1, stdout=stdout, stderr="variant 2: FAILED\n"
            )
            request = GenerationRequest(
                kind="image", prompt="p", aspect_ratio="1:1", model="gpt-image-2",
                output_dir=out_dir, n=2,
            )
            with mock.patch.object(arcads_backend, "_ENV_FILE", fake_env), mock.patch(
                "adapters.arcads_backend.subprocess.run", return_value=fake_proc
            ):
                results = ArcadsBackend().generate(request)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].variant, 1)

    def test_malformed_stdout_line_raises_json_decode_error(self) -> None:
        # Documents current behavior: generate() does not guard json.loads() per line —
        # a malformed line raises rather than being silently skipped or reported as a
        # clean failure. Characterizing this, not asserting it's ideal.
        with tempfile.TemporaryDirectory() as d:
            fake_env = _valid_env(d)
            fake_proc = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="not valid json\n", stderr=""
            )
            request = GenerationRequest(
                kind="image", prompt="p", aspect_ratio="1:1", model="gpt-image-2",
                output_dir=Path(d) / "generated",
            )
            with mock.patch.object(arcads_backend, "_ENV_FILE", fake_env), mock.patch(
                "adapters.arcads_backend.subprocess.run", return_value=fake_proc
            ):
                with self.assertRaises(json.JSONDecodeError):
                    ArcadsBackend().generate(request)


if __name__ == "__main__":
    unittest.main()
