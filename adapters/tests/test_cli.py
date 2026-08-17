"""Tests for the terminal entry point (adapters/cli.py) — the path a skill actually
invokes. Verifies backend selection, the cost-confirmation dry-run gate, the
product-fidelity guard firing at the CLI layer, and a real end-to-end local
compositor run. No network calls anywhere in this file.
"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from adapters import cli
from adapters.base import (
    Capability,
    CostEstimate,
    CreativeBackend,
    CredentialStatus,
    GenerationRequest,
    GenerationResult,
)


class _RecordingBackend(CreativeBackend):
    """A fake backend that records the request it was asked to generate, so tests
    can assert the CLI built and routed it correctly without touching a real vendor."""

    name = "fake"

    def __init__(self, *, creds_ok: bool = True, caps=frozenset({Capability.IMAGE, Capability.IMAGE_EDIT})):
        self._creds_ok = creds_ok
        self._caps = caps
        self.last_request: GenerationRequest | None = None

    def capabilities(self) -> frozenset[Capability]:
        return self._caps

    def check_credentials(self) -> CredentialStatus:
        return CredentialStatus(self._creds_ok, "ok" if self._creds_ok else "missing key")

    def estimate_cost(self, request: GenerationRequest):
        return CostEstimate(amount=1.23, unit="usd", source="unknown")

    def generate(self, request: GenerationRequest):
        self.last_request = request
        return [
            GenerationResult(
                variant=1, path=Path("fake-out.png"), backend=self.name, model="fake-model", cost=None
            )
        ]


def _run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


class TestListCommand(unittest.TestCase):
    def test_list_runs_and_mentions_local_compositor(self) -> None:
        code, out, _ = _run(["list"])
        self.assertEqual(code, 0)
        self.assertIn("local_compositor", out)
        self.assertIn("arcads", out)


class TestGenerateBackendSelection(unittest.TestCase):
    def test_selects_backend_by_name_and_builds_matching_request(self) -> None:
        fake = _RecordingBackend()
        with mock.patch("adapters.cli.get_backend", return_value=fake) as get_backend_mock:
            code, out, _ = _run(
                [
                    "generate",
                    "--backend", "totally-fake-backend",
                    "--model", "some-model",
                    "--kind", "image",
                    "--prompt", "a nice ad",
                    "--aspect-ratio", "1:1",
                    "--n", "2",
                    "--out", "./generated",
                    "--confirm-cost",
                ]
            )
        get_backend_mock.assert_called_once_with("totally-fake-backend")
        self.assertEqual(code, 0)
        self.assertIsNotNone(fake.last_request)
        self.assertEqual(fake.last_request.kind, "image")
        self.assertEqual(fake.last_request.prompt, "a nice ad")
        self.assertEqual(fake.last_request.aspect_ratio, "1:1")
        self.assertEqual(fake.last_request.model, "some-model")
        self.assertEqual(fake.last_request.n, 2)
        self.assertIn("fake-out.png", out)

    def test_missing_credentials_blocks_before_generate_is_called(self) -> None:
        fake = _RecordingBackend(creds_ok=False)
        with mock.patch("adapters.cli.get_backend", return_value=fake):
            code, _, err = _run(
                ["generate", "--backend", "x", "--prompt", "p", "--aspect-ratio", "1:1", "--confirm-cost"]
            )
        self.assertEqual(code, 2)
        self.assertIsNone(fake.last_request)  # generate() was never reached
        self.assertIn("missing key", err)

    def test_dry_run_without_confirm_cost_does_not_call_generate(self) -> None:
        fake = _RecordingBackend()
        with mock.patch("adapters.cli.get_backend", return_value=fake):
            code, _, err = _run(
                ["generate", "--backend", "x", "--prompt", "p", "--aspect-ratio", "1:1"]
            )
        self.assertEqual(code, 0)
        self.assertIsNone(fake.last_request)
        self.assertIn("dry run", err)
        self.assertIn("1.23 usd", err)


class TestGenerateProductLockAtCliLayer(unittest.TestCase):
    def test_product_lock_without_source_is_rejected(self) -> None:
        fake = _RecordingBackend()
        with mock.patch("adapters.cli.get_backend", return_value=fake):
            code, _, err = _run(
                ["generate", "--backend", "x", "--prompt", "p", "--product-lock", "--confirm-cost"]
            )
        self.assertEqual(code, 2)
        self.assertIn("--product-lock requires --source", err)

    def test_product_lock_with_full_generation_kind_is_refused(self) -> None:
        """The CLI-layer equivalent of the backend-layer regression test in
        test_product_fidelity_enforcement.py — proves the terminal command itself,
        not just the library, refuses to let a locked product go through kind='image'."""
        with tempfile.NamedTemporaryFile(suffix=".jpg") as tmp:
            fake = _RecordingBackend(caps=frozenset({Capability.IMAGE}))  # no IMAGE_EDIT
            with mock.patch("adapters.cli.get_backend", return_value=fake):
                code, _, err = _run(
                    [
                        "generate", "--backend", "x", "--kind", "image", "--prompt", "p",
                        "--source", tmp.name, "--product-lock", "--confirm-cost",
                    ]
                )
            self.assertEqual(code, 2)
            self.assertIn("product-fidelity violation", err)
            self.assertIsNone(fake.last_request)  # never reached generate()


class TestComposeCommandEndToEnd(unittest.TestCase):
    def test_real_local_run_produces_file_and_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            product = Path(d) / "product.png"
            background = Path(d) / "background.png"
            output = Path(d) / "out" / "result.png"
            Image.new("RGB", (300, 300), (12, 34, 56)).save(product, "PNG")
            Image.new("RGB", (1600, 1200), (200, 200, 200)).save(background, "PNG")

            fake_log = Path(d) / "adapter-calls.jsonl"
            with mock.patch.object(cli, "_ADAPTER_LOG", fake_log):
                code, out, _ = _run(
                    [
                        "compose",
                        "--product", str(product),
                        "--background", str(background),
                        "--output", str(output),
                        "--aspect", "9:16",
                    ]
                )

            self.assertEqual(code, 0, out)
            self.assertTrue(output.exists())
            with Image.open(output) as img:
                self.assertEqual(img.size, (1080, 1920))

            payload = json.loads(out.strip().splitlines()[-1])
            self.assertEqual(payload["backend"], "local_compositor")
            self.assertIn("product_sha256", payload["provenance"])
            self.assertTrue(fake_log.exists())
            record = json.loads(fake_log.read_text().splitlines()[-1])
            self.assertEqual(record["backend"], "local_compositor")
            # Regression: compose renames the backend's output file to --output
            # *after* generate() returns, so the logged hash must be recomputed
            # against the final path — not the stale (by-then-nonexistent) one.
            self.assertEqual(record["output_path"], str(output))
            self.assertIsNotNone(record["output_sha256"])
            import hashlib

            self.assertEqual(record["output_sha256"], hashlib.sha256(output.read_bytes()).hexdigest())

    def test_missing_product_file_fails_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            background = Path(d) / "background.png"
            Image.new("RGB", (400, 400), (1, 1, 1)).save(background, "PNG")
            code, _, err = _run(
                [
                    "compose",
                    "--product", str(Path(d) / "does-not-exist.png"),
                    "--background", str(background),
                    "--output", str(Path(d) / "out.png"),
                ]
            )
        self.assertEqual(code, 2)
        self.assertIn("product image not found", err)


if __name__ == "__main__":
    unittest.main()
