from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
