from __future__ import annotations

import unittest

from adapters.base import GenerationRequest
from adapters.registry import BACKENDS, get_backend, list_backends


class TestRegistry(unittest.TestCase):
    def test_all_six_backends_registered(self) -> None:
        self.assertEqual(
            set(BACKENDS),
            {"arcads", "openai", "runway", "remotion", "canva", "descript"},
        )

    def test_get_unknown_backend_raises(self) -> None:
        with self.assertRaises(KeyError):
            get_backend("does-not-exist")

    def test_get_known_backend(self) -> None:
        backend = get_backend("openai")
        self.assertEqual(backend.name, "openai")

    def test_only_arcads_marked_executable(self) -> None:
        entries = {e["name"]: e for e in list_backends()}
        self.assertTrue(entries["arcads"]["executable"])
        for name in ("openai", "runway", "remotion", "canva", "descript"):
            self.assertFalse(entries[name]["executable"], f"{name} should not be executable")

    def test_list_backends_makes_no_network_call(self) -> None:
        # Just exercising it end-to-end offline is the assertion — if any backend's
        # check_credentials() tried a network call, this would hang or error in a
        # sandboxed test environment.
        entries = list_backends()
        self.assertEqual(len(entries), 6)

    def test_stub_backend_generate_raises_not_implemented(self) -> None:
        backend = get_backend("runway")
        req = GenerationRequest(kind="image", prompt="p", aspect_ratio="1:1")
        with self.assertRaises(NotImplementedError):
            backend.generate(req)


if __name__ == "__main__":
    unittest.main()
