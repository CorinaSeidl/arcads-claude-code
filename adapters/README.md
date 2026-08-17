# adapters/ — vendor-neutral creative-backend layer

Read `docs/adapter/DESIGN.md` first for the full audit and rationale. This is the
implementation.

## What's real vs. stub

| Backend | Status | Capabilities declared | Needs |
|---|---|---|---|
| `arcads` | **Real** — wraps existing, tested `skills/*/scripts/generate_image.py` | image, image_edit | `.env` with `ARCADS_BASIC_AUTH` or `ARCADS_API_KEY` |
| `openai` | Stub — raises `NotImplementedError` | image, image_edit | `OPENAI_API_KEY` + an implemented request path (not written) |
| `runway` | Stub | video | `RUNWAY_API_KEY` + an implemented request path (not written) |
| `remotion` | Stub | video_edit | Local Node.js + `npx remotion` project (not scaffolded) |
| `canva` | Stub | image_edit | `CANVA_ACCESS_TOKEN` + an implemented request path (not written) |
| `descript` | Stub | video_edit | Descript API access + an implemented request path (not written) |

Only `arcads` executes anything. Every other backend's `generate()` call raises
`NotImplementedError` with a message saying exactly what's missing — nothing here
pretends to call a vendor this repo has never talked to.

Arcads' own video models (Seedance/Sora/Veo/Kling) are also **not** wired through this
adapter yet — `ArcadsBackend.capabilities()` only declares `image`/`image_edit`. Skills
that need Arcads video should keep following `skills/arcads-external-api/SKILL.md`
directly until a later phase adds video support here.

## Check what's usable right now (no network calls)

```bash
python3 -m adapters list
```

This reads `.env` locally and reports credential status per backend — it never makes
an HTTP request.

## Using it from Python

```python
from pathlib import Path
from adapters.registry import get_backend
from adapters.base import GenerationRequest
from adapters.policy import CostGate, ProductFidelityGuard

backend = get_backend("arcads")
cred = backend.check_credentials()
if not cred.ok:
    raise SystemExit(cred.detail)

request = GenerationRequest(
    kind="image",
    prompt="...",
    aspect_ratio="1:1",
    model="nano-banana-2",
    output_dir=Path("./generated"),
)

estimate = backend.estimate_cost(request)  # may be None — Arcads has no billing endpoint
gate = CostGate()
gate.confirm()  # only after showing `estimate` to the user and getting explicit yes
gate.require()

results = backend.generate(request)
```

For a request that must preserve exact product pixels (labels/packaging/SKU text),
set `product_lock` and run it through the guard before calling `generate()`:

```python
from adapters.base import ProductLock

request = GenerationRequest(
    kind="image_edit",
    prompt="Place the product on a marble kitchen counter at golden hour, "
           "do not alter the product itself.",
    source=Path("references/products/hero-front.jpg"),
    product_lock=ProductLock(canonical_image=Path("references/products/hero-front.jpg")),
    model="nano-banana-edit",
)
ProductFidelityGuard.check(backend, request)  # raises ProductFidelityViolation if unsafe
results = backend.generate(request)
```

## Adding a new backend for real

1. Get the vendor's credentials yourself — this repo (and the assistant) cannot obtain
   or validate them for you, and will not make billable calls to test a new backend.
2. Implement request-building in a new `adapters/<vendor>_backend.py` subclassing
   `CreativeBackend` directly (not `UnimplementedBackend`).
3. Update `adapters/registry.py`'s `BACKENDS` dict.
4. Add offline unit tests under `adapters/tests/` for argument-construction/parsing
   logic that don't require live network access.
5. Validate the real request path yourself against your own account before trusting it
   in a skill.
6. Only then update a skill's SKILL.md to route through `adapters.registry` for that
   backend, and update the status table above.

## Tests

```bash
python3 -m unittest discover adapters/tests -v
```

No test in this suite makes a network call.
