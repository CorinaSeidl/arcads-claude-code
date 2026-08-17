"""Full local production job runner: manifest -> validated, QA'd, provenanced,
approval-ready asset package. See docs/adapter/DESIGN.md § 8 (Phase 3).

Stops before any publication/distribution step — this repo's meta-ad-builder skill
(a separate, untouched skill) is where a human decides to actually publish something.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from adapters.base import GenerationResult, ProductFidelityViolation
from adapters.job import CreativeJob
from adapters.local_compositor_backend import CANVAS_SIZES
from adapters.policy import ProductFidelityGuard, build_provenance, sha256_of, write_provenance
from adapters.qa import QAReport, run_qa
from adapters.registry import get_backend

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ADAPTER_LOG = _REPO_ROOT / "logs" / "adapter-calls.jsonl"

# Phase 3 scope, deliberately narrow: no network/vendor backend runs through run-job
# yet, regardless of what a manifest asks for. This is enforced here, not just
# documented — see adapters/tests/test_job_runner.py for the regression test proving
# a manifest requesting "arcads" is refused before any subprocess/network attempt.
SUPPORTED_BACKENDS_FOR_RUN_JOB = frozenset({"local_compositor"})


@dataclass(frozen=True)
class JobResult:
    status: Literal["PASS", "FAIL"]
    reason: str
    job_id: str
    backend: str
    output_path: Path | None
    qa: QAReport | None
    provenance_path: Path | None
    approval_manifest_path: Path | None


def _write_approval_manifest(
    job: CreativeJob,
    status: str,
    reason: str,
    result: GenerationResult | None,
    qa: QAReport | None,
    provenance_path: Path | None,
) -> Path:
    approval = {
        "job_id": job.job_id,
        "channel": job.channel,
        "status": status,
        "reason": reason,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "backend": result.backend if result else job.backend,
        "model": result.model if result else None,
        "output_path": str(result.path) if result else None,
        "qa": qa.as_dict() if qa else None,
        "provenance_path": str(provenance_path) if provenance_path else None,
        "requested_by": job.provenance_seed.requested_by or None,
        "notes": job.provenance_seed.notes or None,
    }
    job.output_dir.mkdir(parents=True, exist_ok=True)
    path = job.output_dir / "approval-manifest.json"
    path.write_text(json.dumps(approval, indent=2))
    return path


def _fail(job: CreativeJob, reason: str) -> JobResult:
    approval_path = _write_approval_manifest(job, "FAIL", reason, None, None, None)
    return JobResult(
        status="FAIL",
        reason=reason,
        job_id=job.job_id,
        backend=job.backend,
        output_path=None,
        qa=None,
        provenance_path=None,
        approval_manifest_path=approval_path,
    )


def run_job(job: CreativeJob) -> JobResult:
    # 1. Backend allowlist — the Phase 3 boundary. Checked before touching the
    # registry/credentials/network at all.
    if job.backend not in SUPPORTED_BACKENDS_FOR_RUN_JOB:
        return _fail(
            job,
            f"backend {job.backend!r} is not enabled for run-job in this phase "
            f"(only {sorted(SUPPORTED_BACKENDS_FOR_RUN_JOB)} — no network/vendor "
            "backend is wired into run-job yet; a product-locked job in particular "
            "must never be routed at a backend that could redraw the product).",
        )

    # 2. Input validation.
    if job.aspect_ratio not in CANVAS_SIZES:
        return _fail(
            job, f"unsupported aspect_ratio {job.aspect_ratio!r}; supported: {sorted(CANVAS_SIZES)}"
        )
    if not job.product.asset.exists():
        return _fail(job, f"product asset not found: {job.product.asset}")
    if job.background is None:
        return _fail(job, "local_compositor requires a background asset; none was supplied")
    if not job.background.asset.exists():
        return _fail(job, f"background asset not found: {job.background.asset}")

    backend = get_backend(job.backend)
    cred = backend.check_credentials()
    if not cred.ok:
        return _fail(job, f"backend {job.backend!r} not usable: {cred.detail}")

    request = job.to_generation_request()

    # 3. Product-fidelity guard — real enforcement at the real job-execution entry
    # point, on top of the defense-in-depth already inside backend.generate() itself.
    if request.product_lock is not None:
        try:
            ProductFidelityGuard.check(backend, request)
        except ProductFidelityViolation as e:
            return _fail(job, f"product-fidelity violation: {e}")

    pre_run_product_sha256 = sha256_of(job.product.asset)

    # 4. Generate.
    try:
        results = backend.generate(request)
    except Exception as e:  # noqa: BLE001 — surface as a clean FAIL, not a crash
        return _fail(job, f"generation failed: {e}")
    if not results:
        return _fail(job, "backend returned no results")
    result = results[0]

    # 5. QA.
    qa_report = run_qa(job, request, result, pre_run_product_sha256)

    # 6. Provenance — shared cross-job log (existing convention) + a self-contained
    # per-job file so the approval package doesn't require grepping a shared log.
    record = build_provenance(result, request)
    record["job_id"] = job.job_id
    record["channel"] = job.channel
    record["requested_by"] = job.provenance_seed.requested_by or None
    record["notes"] = job.provenance_seed.notes or None
    record["qa_passed"] = qa_report.passed
    write_provenance(record, _ADAPTER_LOG)
    provenance_path = job.output_dir / "provenance.json"
    provenance_path.write_text(json.dumps(record, indent=2))

    # 7. Approval manifest — always written, PASS or FAIL, so nothing is silently lost.
    status = "PASS" if qa_report.passed else "FAIL"
    reason = "ok" if qa_report.passed else "one or more QA checks failed — see qa.checks"
    approval_path = _write_approval_manifest(job, status, reason, result, qa_report, provenance_path)

    return JobResult(
        status=status,
        reason=reason,
        job_id=job.job_id,
        backend=result.backend,
        output_path=result.path,
        qa=qa_report,
        provenance_path=provenance_path,
        approval_manifest_path=approval_path,
    )
