from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from adapters.base import (
    Capability,
    CostEstimate,
    CreativeBackend,
    CredentialStatus,
    GenerationRequest,
    GenerationResult,
    ProductFidelityViolation,
    ProductLock,
)
from adapters.policy import (
    CostConfirmationRequired,
    CostGate,
    ProductFidelityGuard,
    RetryPolicy,
    build_provenance,
    require_still_before_video,
    write_provenance,
)


class _FakeBackend(CreativeBackend):
    name = "fake"

    def __init__(self, caps: frozenset[Capability]) -> None:
        self._caps = caps

    def capabilities(self) -> frozenset[Capability]:
        return self._caps

    def check_credentials(self) -> CredentialStatus:
        return CredentialStatus(True, "ok")

    def estimate_cost(self, request: GenerationRequest):
        return None

    def generate(self, request: GenerationRequest):
        return []


class TestRetryPolicy(unittest.TestCase):
    def test_default_cap_is_two(self) -> None:
        policy = RetryPolicy()
        self.assertTrue(policy.may_retry())
        policy.record_attempt()
        self.assertTrue(policy.may_retry())
        policy.record_attempt()
        self.assertFalse(policy.may_retry())

    def test_custom_cap(self) -> None:
        policy = RetryPolicy(max_retries=0)
        self.assertFalse(policy.may_retry())


class TestCostGate(unittest.TestCase):
    def test_requires_confirmation_first(self) -> None:
        gate = CostGate()
        with self.assertRaises(CostConfirmationRequired):
            gate.require()

    def test_qa_retry_bypasses_confirmation(self) -> None:
        gate = CostGate()
        gate.require(is_qa_retry=True)  # should not raise

    def test_confirm_then_require(self) -> None:
        gate = CostGate()
        gate.confirm()
        gate.require()  # should not raise


class TestStillBeforeVideo(unittest.TestCase):
    def test_blocks_unapproved_start_frame(self) -> None:
        req = GenerationRequest(
            kind="video", prompt="p", start_frame=Path("still.png")
        )
        with self.assertRaises(PermissionError):
            require_still_before_video(req, still_was_approved=False)

    def test_allows_approved_start_frame(self) -> None:
        req = GenerationRequest(
            kind="video", prompt="p", start_frame=Path("still.png")
        )
        require_still_before_video(req, still_was_approved=True)  # should not raise

    def test_ignores_non_video_requests(self) -> None:
        req = GenerationRequest(kind="image", prompt="p", aspect_ratio="1:1")
        require_still_before_video(req, still_was_approved=False)  # should not raise


class TestProductFidelityGuard(unittest.TestCase):
    def test_noop_without_lock(self) -> None:
        req = GenerationRequest(kind="image", prompt="p", aspect_ratio="1:1")
        backend = _FakeBackend(frozenset({Capability.IMAGE}))
        ProductFidelityGuard.check(backend, req)  # should not raise

    def test_raises_when_canonical_image_missing(self) -> None:
        req = GenerationRequest(
            kind="image_edit",
            prompt="p",
            source=Path("/nonexistent/product.jpg"),
            product_lock=ProductLock(canonical_image=Path("/nonexistent/product.jpg")),
        )
        backend = _FakeBackend(frozenset({Capability.IMAGE_EDIT}))
        with self.assertRaises(ProductFidelityViolation):
            ProductFidelityGuard.check(backend, req)

    def test_raises_when_backend_lacks_image_edit(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".jpg") as tmp:
            product = Path(tmp.name)
            req = GenerationRequest(
                kind="image_edit",
                prompt="p",
                source=product,
                product_lock=ProductLock(canonical_image=product),
            )
            backend = _FakeBackend(frozenset({Capability.IMAGE}))  # no IMAGE_EDIT
            with self.assertRaises(ProductFidelityViolation):
                ProductFidelityGuard.check(backend, req)

    def test_passes_with_image_edit_and_existing_file(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".jpg") as tmp:
            product = Path(tmp.name)
            req = GenerationRequest(
                kind="image_edit",
                prompt="p",
                source=product,
                product_lock=ProductLock(canonical_image=product),
            )
            backend = _FakeBackend(frozenset({Capability.IMAGE_EDIT}))
            ProductFidelityGuard.check(backend, req)  # should not raise


class TestProvenance(unittest.TestCase):
    def test_build_provenance_hashes_output_and_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            out_path = Path(d) / "out.png"
            out_path.write_bytes(b"fake-image-bytes")
            req = GenerationRequest(kind="image", prompt="a secret prompt", aspect_ratio="1:1")
            result = GenerationResult(
                variant=1,
                path=out_path,
                backend="fake",
                model="fake-model",
                cost=CostEstimate(amount=1.0, unit="credits", source="unknown"),
            )
            record = build_provenance(result, req)
            self.assertNotIn("a secret prompt", json.dumps(record))
            self.assertIsNotNone(record["output_sha256"])
            self.assertEqual(record["backend"], "fake")
            self.assertEqual(record["cost"]["unit"], "credits")

    def test_build_provenance_handles_missing_output(self) -> None:
        req = GenerationRequest(kind="image", prompt="p", aspect_ratio="1:1")
        result = GenerationResult(
            variant=1, path=Path("/nonexistent/out.png"), backend="fake", model="m", cost=None
        )
        record = build_provenance(result, req)
        self.assertIsNone(record["output_sha256"])
        self.assertIsNone(record["cost"])

    def test_write_provenance_appends_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            log_path = Path(d) / "nested" / "log.jsonl"
            write_provenance({"a": 1}, log_path)
            write_provenance({"a": 2}, log_path)
            lines = log_path.read_text().splitlines()
            self.assertEqual(len(lines), 2)
            self.assertEqual(json.loads(lines[0])["a"], 1)
            self.assertEqual(json.loads(lines[1])["a"], 2)


if __name__ == "__main__":
    unittest.main()
