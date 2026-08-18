"""Terminal entry point for the adapter layer: `python3 -m adapters <command>`.

Commands:
  list      — backend status (capabilities, credentials), no network calls.
  generate  — generic adapter-routed generation. This is the call path skills
              (starting with chatgpt-image-ad) use instead of invoking a
              vendor-specific script directly. Requires --confirm-cost to actually
              run (mirrors the SKILL.md-documented cost-confirmation gate); without
              it, prints the estimate and exits.
  compose   — dedicated local_compositor invocation: paste a canonical product
              image onto a supplied background at a standard social aspect ratio.
              Credential-free, deterministic, no network call, ever.
  run-job   — execute a full creative job manifest end to end: validate, enforce
              ProductLock, generate via local_compositor, run deterministic QA,
              write provenance, and produce an approval-ready package. Stops before
              any publication/distribution step.

See adapters/README.md for full usage, docs/adapter/DESIGN.md § 6 (Phase 2) and § 8
(Phase 3) for why this exists.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from adapters.base import GenerationRequest, ProductFidelityViolation, ProductLock
from adapters.job import JobValidationError, load_job
from adapters.job_runner import run_job
from adapters.policy import (
    CostGate,
    ProductFidelityGuard,
    build_provenance,
    sha256_of,
    write_provenance,
)
from adapters.registry import get_backend, list_backends

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ADAPTER_LOG = _REPO_ROOT / "logs" / "adapter-calls.jsonl"


def _cmd_list(_: argparse.Namespace) -> int:
    for entry in list_backends():
        flag = "✓" if entry["credentials_ok"] else "✗"
        real = "executable" if entry["executable"] else "stub — not wired yet"
        caps = ", ".join(entry["capabilities"]) or "(none declared)"
        print(f"{flag} {entry['name']:<16} [{real}]  capabilities: {caps}")
        print(f"    {entry['detail']}")
    return 0


def _cmd_generate(args: argparse.Namespace) -> int:
    backend = get_backend(args.backend)
    cred = backend.check_credentials()
    if not cred.ok:
        print(f"error: backend {args.backend!r} not usable: {cred.detail}", file=sys.stderr)
        return 2

    product_lock = None
    if args.product_lock:
        if not args.source:
            print("error: --product-lock requires --source <canonical product image>", file=sys.stderr)
            return 2
        product_lock = ProductLock(canonical_image=Path(args.source))

    try:
        request = GenerationRequest(
            kind=args.kind,
            prompt=args.prompt,
            aspect_ratio=args.aspect_ratio,
            references=tuple(Path(p) for p in args.image_ref),
            source=Path(args.source) if args.source else None,
            background=Path(args.background) if args.background else None,
            product_lock=product_lock,
            n=args.n,
            output_dir=Path(args.out),
            model=args.model,
        )
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    # Real enforcement point: a product-locked request is checked here, in the actual
    # CLI path a skill invokes — not only inside the backend (which also self-checks,
    # see adapters/arcads_backend.py, for defense-in-depth).
    if request.product_lock is not None:
        try:
            ProductFidelityGuard.check(backend, request)
        except ProductFidelityViolation as e:
            print(f"error: product-fidelity violation: {e}", file=sys.stderr)
            return 2

    estimate = backend.estimate_cost(request)
    print(
        f"cost estimate: {estimate if estimate is not None else 'unknown — no historical data, ask the user'}",
        file=sys.stderr,
    )
    if not args.confirm_cost:
        print(
            "dry run only — no generation performed. Re-run with --confirm-cost after "
            "showing this estimate to the user and getting explicit approval.",
            file=sys.stderr,
        )
        return 0

    gate = CostGate()
    gate.confirm()
    gate.require()

    results = backend.generate(request)
    for result in results:
        record = build_provenance(result, request)
        write_provenance(record, _ADAPTER_LOG)
        print(
            json.dumps(
                {
                    "variant": result.variant,
                    "path": str(result.path),
                    "backend": result.backend,
                    "model": result.model,
                    "cost": str(result.cost) if result.cost else None,
                }
            )
        )
    return 0


def _cmd_compose(args: argparse.Namespace) -> int:
    backend = get_backend("local_compositor")
    product = Path(args.product)
    background = Path(args.background)
    output = Path(args.output)

    if not product.exists():
        print(f"error: product image not found: {product}", file=sys.stderr)
        return 2
    if not background.exists():
        print(f"error: background image not found: {background}", file=sys.stderr)
        return 2

    cred = backend.check_credentials()
    if not cred.ok:
        print(f"error: local_compositor not usable: {cred.detail}", file=sys.stderr)
        return 2

    try:
        request = GenerationRequest(
            kind="image_edit",
            prompt=(
                "local composite — product pixels preserved, environment supplied "
                "externally (no generative model call)"
            ),
            aspect_ratio=args.aspect,
            source=product,
            background=background,
            output_dir=output.parent if str(output.parent) else Path("."),
            n=1,
            model="deterministic-compositor-v1",
            product_lock=ProductLock(canonical_image=product),
            options={"mode": args.mode, "position": args.position, "margin": args.margin},
        )
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    # Real enforcement, even though local_compositor is fidelity-safe by construction —
    # this is the literal "ProductFidelityGuard operational in the real skill path"
    # requirement exercised through the actual terminal command, not just a unit test.
    try:
        ProductFidelityGuard.check(backend, request)
    except ProductFidelityViolation as e:
        print(f"error: product-fidelity violation: {e}", file=sys.stderr)
        return 2

    try:
        results = backend.generate(request)
    except Exception as e:  # noqa: BLE001 — surface compositor errors plainly to the terminal
        print(f"error: {e}", file=sys.stderr)
        return 1

    result = results[0]
    output.parent.mkdir(parents=True, exist_ok=True)
    if result.path != output:
        result_path = result.path.replace(output)  # Path.replace = deterministic rename/move
    else:
        result_path = result.path

    # build_provenance() hashes result.path — but that file has just been renamed to
    # `output`, so its own hash would come back None. Recompute against the real
    # final location instead of trusting the pre-rename snapshot.
    record = build_provenance(result, request)
    record["output_path"] = str(result_path)
    record["output_sha256"] = sha256_of(result_path)
    write_provenance(record, _ADAPTER_LOG)

    print(
        json.dumps(
            {
                "path": str(result_path),
                "backend": result.backend,
                "model": result.model,
                "cost": str(result.cost) if result.cost else None,
                "product_pixels_exact": result.provenance.get("product_pixels_exact"),
                "provenance": result.provenance,
            }
        )
    )
    return 0


def _cmd_run_job(args: argparse.Namespace) -> int:
    job_file = Path(args.job_file)
    try:
        job = load_job(job_file)
    except JobValidationError as e:
        print(f"error: invalid job manifest: {e}", file=sys.stderr)
        return 2

    result = run_job(job)

    status_flag = "PASS" if result.status == "PASS" else "FAIL"
    print(f"{'✓' if status_flag == 'PASS' else '✗'} {status_flag} — job {result.job_id!r}", file=sys.stderr)
    print(f"  reason            : {result.reason}", file=sys.stderr)
    print(f"  backend selected  : {result.backend}", file=sys.stderr)
    print(f"  output path       : {result.output_path or '(none — failed before generation)'}", file=sys.stderr)
    if result.qa is not None:
        n_pass = sum(1 for c in result.qa.checks if c.passed)
        print(
            f"  QA result         : {'PASS' if result.qa.passed else 'FAIL'} "
            f"({n_pass}/{len(result.qa.checks)} checks passed)",
            file=sys.stderr,
        )
        for c in result.qa.checks:
            mark = "ok  " if c.passed else "FAIL"
            print(f"      [{mark}] {c.name}: {c.detail}", file=sys.stderr)
    else:
        print("  QA result         : (not run — failed before generation)", file=sys.stderr)
    print(f"  provenance path   : {result.provenance_path or '(none)'}", file=sys.stderr)
    print(f"  approval manifest : {result.approval_manifest_path or '(none)'}", file=sys.stderr)

    print(
        json.dumps(
            {
                "status": result.status,
                "job_id": result.job_id,
                "backend": result.backend,
                "output_path": str(result.output_path) if result.output_path else None,
                "qa_passed": result.qa.passed if result.qa else None,
                "provenance_path": str(result.provenance_path) if result.provenance_path else None,
                "approval_manifest_path": (
                    str(result.approval_manifest_path) if result.approval_manifest_path else None
                ),
            }
        )
    )
    return 0 if result.status == "PASS" else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m adapters")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="show backend status (offline, no network calls)")
    p_list.set_defaults(func=_cmd_list)

    p_gen = sub.add_parser(
        "generate", help="generic adapter-routed generation (the skill call path)"
    )
    p_gen.add_argument("--backend", required=True, help="e.g. arcads, local_compositor")
    p_gen.add_argument("--model", help="backend-specific model hint, e.g. gpt-image-2")
    p_gen.add_argument("--kind", default="image", choices=["image", "image_edit", "video"])
    p_gen.add_argument("--prompt", required=True)
    p_gen.add_argument("--aspect-ratio", dest="aspect_ratio")
    p_gen.add_argument("--source", help="source image, required for kind=image_edit")
    p_gen.add_argument("--background", help="local background image (local_compositor)")
    p_gen.add_argument("--image-ref", action="append", default=[], dest="image_ref")
    p_gen.add_argument("--n", type=int, default=1)
    p_gen.add_argument("--out", default="./generated")
    p_gen.add_argument(
        "--product-lock",
        action="store_true",
        help="require pixel-preserving routing for --source (see docs/adapter/PRODUCT_FIDELITY.md)",
    )
    p_gen.add_argument(
        "--confirm-cost",
        action="store_true",
        help="actually run generation; without this flag, only prints the cost estimate",
    )
    p_gen.set_defaults(func=_cmd_generate)

    p_compose = sub.add_parser(
        "compose", help="composite a canonical product image onto a local background (no vendor call)"
    )
    p_compose.add_argument("--product", required=True, help="canonical local product image path")
    p_compose.add_argument(
        "--background", required=True, help="local background image (from any source)"
    )
    p_compose.add_argument("--output", required=True, help="output PNG path")
    p_compose.add_argument(
        "--aspect", default="1:1", help="1:1, 9:16, 16:9, 4:5, or 5:4"
    )
    p_compose.add_argument("--mode", default="fit", choices=["fit", "native"])
    p_compose.add_argument("--position", default="center", choices=["center", "bottom"])
    p_compose.add_argument("--margin", type=float, default=0.08)
    p_compose.set_defaults(func=_cmd_compose)

    p_run_job = sub.add_parser(
        "run-job",
        help="execute a full creative job manifest: validate, QA, provenance, approval package",
    )
    p_run_job.add_argument("job_file", help="path to a job manifest JSON file, e.g. jobs/<name>.json")
    p_run_job.set_defaults(func=_cmd_run_job)

    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
