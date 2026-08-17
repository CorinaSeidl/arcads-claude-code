"""Arcads backend adapter — the ONE real, executable backend in this repo.

Does not reimplement Arcads' HTTP calls. Shells out to the existing, tested
skills/chatgpt-image-ad/scripts/generate_image.py and
skills/nano-banana-image-ad/scripts/generate_image.py exactly as a human would run
them from the terminal, so there is exactly one place that owns Arcads
request-building and it can't drift from what the skills document.

Phase 1 scope: image family only (IMAGE / IMAGE_EDIT). Arcads video generation
(Seedance/Sora/Veo/Kling) is not wired through this adapter yet — skills needing video
should keep following skills/arcads-external-api/SKILL.md directly until a
video-capable adapter phase lands (see docs/adapter/DESIGN.md § 6, future phases).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from adapters.base import (
    Capability,
    CostEstimate,
    CreativeBackend,
    CredentialStatus,
    GenerationRequest,
    GenerationResult,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CHATGPT_SCRIPT = _REPO_ROOT / "skills" / "chatgpt-image-ad" / "scripts" / "generate_image.py"
_NANO_BANANA_SCRIPT = _REPO_ROOT / "skills" / "nano-banana-image-ad" / "scripts" / "generate_image.py"
_ARCADS_LOG = _REPO_ROOT / "logs" / "arcads-api.jsonl"
_ENV_FILE = _REPO_ROOT / ".env"

_PLACEHOLDER_MARKERS = ("your_base64_encoded_credentials_here", "your_key_here")


def _script_for_model(model: str) -> Path:
    if model.startswith("gpt-image"):
        return _CHATGPT_SCRIPT
    if model.startswith("nano-banana"):
        return _NANO_BANANA_SCRIPT
    raise ValueError(
        f"arcads backend: no known generator script for model {model!r}. "
        "Expected a 'gpt-image*' or 'nano-banana*' model name — Arcads video models "
        "(seedance-2.0, sora2, veo31, kling-*) are not wired through this adapter yet."
    )


class ArcadsBackend(CreativeBackend):
    name = "arcads"

    def capabilities(self) -> frozenset[Capability]:
        return frozenset({Capability.IMAGE, Capability.IMAGE_EDIT})

    def check_credentials(self) -> CredentialStatus:
        if not _ENV_FILE.exists():
            return CredentialStatus(
                False, f".env not found at {_ENV_FILE} — run ./scripts/setup.sh"
            )
        text = _ENV_FILE.read_text()
        has_basic = "ARCADS_BASIC_AUTH=" in text and not any(
            marker in text for marker in _PLACEHOLDER_MARKERS
        )
        has_key = "ARCADS_API_KEY=" in text and "your_key_here" not in text
        if has_basic or has_key:
            return CredentialStatus(True, "ARCADS_BASIC_AUTH or ARCADS_API_KEY found in .env")
        return CredentialStatus(
            False,
            "neither ARCADS_BASIC_AUTH nor ARCADS_API_KEY is set to a real value in .env "
            "(only placeholder text found)",
        )

    def estimate_cost(self, request: GenerationRequest) -> CostEstimate | None:
        """Historical-log lookup only — Arcads has no billing/quote endpoint.
        Mirrors skills/arcads-external-api/SKILL.md 'Cost data sources' priority 1."""
        if not _ARCADS_LOG.exists() or not request.model:
            return None
        amounts: list[float] = []
        for line in _ARCADS_LOG.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("model") != request.model:
                continue
            response = entry.get("response") or {}
            if response.get("status") != "generated":
                continue
            credits = response.get("creditsCharged")
            if isinstance(credits, (int, float)):
                amounts.append(float(credits))
        if not amounts:
            return None
        avg = sum(amounts) / len(amounts)
        return CostEstimate(
            amount=avg,
            unit="credits",
            source="historical_log",
            note=f"avg of {len(amounts)} prior '{request.model}' call(s) in {_ARCADS_LOG.name}",
        )

    def generate(self, request: GenerationRequest) -> list[GenerationResult]:
        if request.kind not in ("image", "image_edit"):
            raise ValueError(
                f"arcads backend (phase 1) only supports kind='image'/'image_edit', "
                f"got {request.kind!r}"
            )
        if not request.model:
            raise ValueError("arcads backend requires request.model (e.g. 'gpt-image-2', "
                              "'nano-banana-2', 'nano-banana-pro', 'nano-banana-edit')")

        cred = self.check_credentials()
        if not cred.ok:
            raise RuntimeError(f"arcads backend: {cred.detail}")

        script = _script_for_model(request.model)
        if not script.exists():
            raise RuntimeError(f"arcads backend: generator script missing: {script}")

        argv = [
            sys.executable,
            str(script),
            "--prompt", request.prompt,
            "--mode", "image_edit" if request.kind == "image_edit" else "image",
            "--n", str(request.n),
            "--out", str(request.output_dir),
            "--model", request.model,
            "--env-file", str(_ENV_FILE),
        ]
        if request.kind == "image":
            if not request.aspect_ratio:
                raise ValueError("aspect_ratio is required for kind='image'")
            argv += ["--aspect-ratio", request.aspect_ratio]
        else:
            assert request.source is not None  # enforced by GenerationRequest.__post_init__
            argv += ["--source", str(request.source)]
        for ref in request.references:
            argv += ["--image-ref", str(ref)]

        proc = subprocess.run(argv, capture_output=True, text=True, check=False)
        if proc.returncode == 2:
            raise ValueError(f"arcads backend: bad arguments — {proc.stderr.strip()}")
        if proc.returncode == 1 and not proc.stdout.strip():
            raise RuntimeError(f"arcads backend: all variants failed — {proc.stderr.strip()}")

        results: list[GenerationResult] = []
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            results.append(
                GenerationResult(
                    variant=obj["variant"],
                    path=Path(obj["path"]),
                    backend=self.name,
                    model=obj.get("model", request.model),
                    cost=None,  # Arcads exposes no billing endpoint; only historical-log estimates exist
                    provenance={
                        "arcads_asset_id": obj.get("asset_id"),
                        "mode": obj.get("mode"),
                        "aspect_ratio": obj.get("aspect_ratio"),
                        "stderr_tail": proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else None,
                    },
                )
            )
        return results
