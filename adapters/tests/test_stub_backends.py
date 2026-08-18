from __future__ import annotations

import os
import unittest
from unittest import mock

from adapters.base import GenerationRequest
from adapters.canva_backend import CanvaBackend
from adapters.descript_backend import DescriptBackend
from adapters.openai_backend import OpenAIBackend
from adapters.remotion_backend import RemotionBackend
from adapters.runway_backend import RunwayBackend

STUB_CLASSES = [OpenAIBackend, RunwayBackend, RemotionBackend, CanvaBackend, DescriptBackend]


class TestStubBackends(unittest.TestCase):
    def setUp(self) -> None:
        # Ensure a clean env regardless of the host machine's shell state.
        self._env_patch = mock.patch.dict(os.environ, {}, clear=True)
        self._env_patch.start()

    def tearDown(self) -> None:
        self._env_patch.stop()

    def test_all_stubs_raise_not_implemented(self) -> None:
        req = GenerationRequest(kind="image", prompt="p", aspect_ratio="1:1")
        for cls in STUB_CLASSES:
            backend = cls()
            with self.assertRaises(NotImplementedError, msg=cls.__name__):
                backend.generate(req)

    def test_all_stubs_declare_nonempty_capabilities(self) -> None:
        for cls in STUB_CLASSES:
            backend = cls()
            self.assertTrue(backend.capabilities(), msg=cls.__name__)

    def test_all_stubs_report_missing_credentials_without_env(self) -> None:
        for cls in STUB_CLASSES:
            backend = cls()
            status = backend.check_credentials()
            if backend.required_env:
                self.assertFalse(status.ok, msg=cls.__name__)

    def test_all_stubs_estimate_cost_none(self) -> None:
        req = GenerationRequest(kind="image", prompt="p", aspect_ratio="1:1")
        for cls in STUB_CLASSES:
            self.assertIsNone(cls().estimate_cost(req), msg=cls.__name__)

    def test_remotion_has_no_required_env_but_still_unimplemented(self) -> None:
        # Remotion is a local tool, not an API-key vendor — check_credentials()
        # should not claim "ok" just because no env vars are required.
        backend = RemotionBackend()
        self.assertEqual(backend.required_env, ())
        status = backend.check_credentials()
        self.assertFalse(status.ok)


if __name__ == "__main__":
    unittest.main()
