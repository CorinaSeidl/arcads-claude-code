# Vendor-neutral Operations OS adaptation — design doc

Status: Phase 3 complete (full local production job contract, end to end).
Branch: `feature/vendor-neutral-adapter-layer`. See § 7 for Phase 2, § 8 for Phase 3.

This repo (`arcads-claude-code`) currently ships a terminal-first, Claude-Code-driven
creative-production workflow that is hardwired to one vendor: Arcads
(`https://external-api.arcads.ai`). We do not have an Arcads subscription today. This
doc audits what's actually vendor-specific vs. reusable, and proposes the smallest safe
adapter layer that lets skills route to OpenAI, Runway, Remotion, Canva, Descript, or a
future Arcads key — without rewriting the skills themselves or deleting anything that
works today.

## 1. Architecture map

```
arcads-claude-code/
├── CLAUDE.md, AGENTS.md, README.md      — agent instructions + human docs (generated
│                                          from *.tail.md fragments; DO NOT EDIT headers)
├── .claude/, .cursor/                   — generated skill copies (gitignored), synced
│                                          from skills/ + shared/skills/ by sync-skill.sh
├── .claude/settings.json                — SessionStart hook: sync-skill.sh + check-context.sh
├── scripts/                             — repo-root setup/check/sync scripts
│   ├── setup.sh                         — writes .env, MASTER_CONTEXT.md, validates auth
│   ├── check-arcads-env.sh              — GET /v1/products smoke test
│   └── sync-skill.sh                    — thin wrapper → shared/scripts/sync-skill.sh
├── shared/                              — content propagated from an upstream
│   │                                      "gen-ai-core" monorepo (per in-file comments);
│   │                                      treat as vendored/upstream-owned, not ours to
│   │                                      restructure — new code should NOT live here.
│   ├── scripts/{sync-skill.sh,check-context.sh}
│   ├── skills/image-ad-prompting/       — shared brain: 37-template prompt library,
│   │                                      safety suffixes, template format (no SKILL.md
│   │                                      → not independently invocable, referenced by
│   │                                      the 3 image-ad skills below)
│   ├── skills/{pixar-style-ad,claymation-ad,caption-video,gemini-omni-flash}/
│   │                                    — cross-API prompting guides + a few scripts
│   │                                      (caption-video is already vendor-neutral:
│   │                                      HyperFrames + Whisper + ffmpeg, no Arcads calls)
│   └── skills/meta-ad-builder/          — the ONE downstream skill that is genuinely
│                                          vendor-neutral already: talks to Meta's
│                                          Graph API, not Arcads. Takes a finished file
│                                          path as input; doesn't care how it was made.
├── skills/                              — canonical Arcads-specific skills (source of
│   │                                      truth; synced into .claude/ and .cursor/)
│   ├── arcads-external-api/             — THE core skill: full Arcads API surface,
│   │                                      per-model prompt libraries, polling rules
│   ├── chatgpt-image-ad/scripts/generate_image.py   — Arcads /v2/images/generate,
│   │                                      model locked to gpt-image-2
│   ├── nano-banana-image-ad/scripts/generate_image.py — same endpoint, nano-banana family
│   ├── image-ad-clone/                  — reverse-engineer an ad → library entry;
│   │                                      routes to one of the two scripts above
│   └── generate-youtube-thumbnail/scripts/generate-batch.sh — Arcads batch bash script
├── references/                          — local-only (gitignored) reference images:
│                                          influencers/, products/, aesthetics/
├── logs/arcads-api.jsonl                — append-only per-call cost/provenance log
└── MASTER_CONTEXT.template.md           — workspace memory template (credit costs,
                                            brand voice, per-API "Project snapshot" block)
```

**Execution model:** there is no application code that "runs" — the repo is a set of
Markdown SKILL.md files (read by the Claude Code / Cursor agent as instructions) plus a
handful of small scripts the agent shells out to. The agent is the orchestrator: it
reads a SKILL.md, decides which script/endpoint to hit, asks the user for approval
gates (dialogue, cost, product choice), calls a script or curl command, polls, runs
visual QA on the result, and writes a log line. All the *process* (approval gates,
retry caps, QA, session folders, cost estimation, provenance logging) lives in prose
inside SKILL.md files — it is enforced by agent instruction-following, not by code.

## 2. Dependency map — every Arcads-specific touch point

| Kind | Where | Detail |
|---|---|---|
| **API base URL** | `skills/arcads-external-api/{SKILL.md,reference.md}`, both image-ad `generate_image.py` scripts, `scripts/check-arcads-env.sh`, `.env.example` | `https://external-api.arcads.ai`, overridable via `ARCADS_BASE_URL` |
| **Credentials** | `.env.example`, `scripts/setup.sh`, `scripts/check-arcads-env.sh`, both `generate_image.py` `auth_header()` fns | `ARCADS_BASIC_AUTH` (pre-encoded `Basic ...`) or `ARCADS_API_KEY` (Basic username, empty password) |
| **Endpoints** | `skills/arcads-external-api/reference.md` | `POST /v2/videos/generate`, `POST /v2/images/generate` (note inconsistent case: `/V2/` also documented), `POST /v1/b-roll`, `POST /v1/scene`, `POST /v1/sora2/remix/video`, `GET /v1/videos/{id}`, `GET /v1/assets/{id}`, `POST /v1/file-upload/get-presigned-url`, `POST /v1/products`, `GET /v1/products`, `POST /v1/folders`, `GET /v1/products/{id}/folders`, `POST /v1/projects`, `POST /v1/assets/add-to-project`, `POST /v1/scripts`, `POST /v1/scripts/{id}/generate`, `POST /v1/omnihuman`, `POST /v1/audio-driven` |
| **Model identifiers** | reference.md, prompt-library files | `seedance-2.0`, `sora2`/`sora2-pro`, `veo31`, `kling-2.6`/`kling-3.0`, `grok-video`, `nano-banana`/`nano-banana-2`/`nano-banana-edit`, `gpt-image-2` (Arcads' proxied name — NOT the same as calling OpenAI directly), `soul`, `grok_image`, `seedream`/`seedream_5_lite` |
| **Executable scripts (hard dependency)** | `skills/chatgpt-image-ad/scripts/generate_image.py`, `skills/nano-banana-image-ad/scripts/generate_image.py`, `skills/generate-youtube-thumbnail/scripts/generate-batch.sh` | All three hardcode `BASE_URL_DEFAULT = "https://external-api.arcads.ai"`, the presigned-upload flow, and `/v2/images/generate` / `/V2/images/generate` request shape. `image-ad-clone` has no script of its own — it *depends on* the two generator scripts above being present. |
| **Config/env** | `.env.example` | `ARCADS_BASIC_AUTH`, `ARCADS_API_KEY`, `ARCADS_CLIENT_ID`, `ARCADS_BASE_URL`, `PRODUCT_ID`/`PROJECT_ID` (read by the image-ad scripts from env, not `.env.example`, but conventionally Arcads product/project UUIDs) |
| **Setup/check scripts** | `scripts/setup.sh`, `scripts/check-arcads-env.sh` | Both validate against `$BASE_URL/v1/products`; setup.sh's credential-probing logic is Arcads-Basic-auth-specific |
| **Session/org model** | `skills/arcads-external-api/SKILL.md` "Session setup" section | Arcads' own folder/project hierarchy (`POST /v1/folders`, `POST /v1/projects`) — a dashboard-organization concept specific to Arcads, not a generic file-organization pattern |
| **Cost model** | `skills/arcads-external-api/SKILL.md` "Credit cost estimation", `logs/arcads-api.jsonl`, `logs/README.md`, `MASTER_CONTEXT.template.md` "Credit costs" table | Arcads' proprietary "credits" unit; no billing endpoint exists, so the whole cost story is agent-side log-mining |
| **Product-context API** | reference.md "Product context via ProductCreationDto" | Arcads product objects carry marketing metadata (`targetAudience`, `mainFeatures`, etc.) referenced by `productId` in every call |
| **Affiliate/signup links** | `skills/arcads-external-api/SKILL.md` "Signup link (affiliate)", README.md, AGENTS.md | `https://arcads.ai/?via=claude-code` — promotional, not technical |
| **Branding / promo content** | README.md (Skool community, walkthrough video, "Mr. Paid Social"), `shared/CLAUDE.md` "surface the community" section, AGENTS.md same section | Non-technical; safe to ignore for the adapter but should not be deleted (out of scope; it's the author's monetization, not a technical dependency) |
| **Meta-ad-builder** | `shared/skills/meta-ad-builder/` | **Not** an Arcads dependency — talks to `graph.facebook.com` directly, takes a finished file path. Already vendor-neutral at the input boundary. |
| **Caption-video** | `shared/skills/caption-video/` | **Not** an Arcads dependency — HyperFrames + Whisper + ffmpeg on a finished mp4. Already vendor-neutral. |

**Cross-API awareness already exists.** `shared/skills/pixar-style-ad/prompting/guide.md`
and its claymation sibling already carry a "Per-API endpoint notes (KIE vs Arcads)"
table — this repo's authors already anticipated a second backend (KIE.ai) at the
prompting-guide layer, just not as a runtime abstraction. That table is useful prior
art for the adapter's per-backend field-mapping documentation.

## 3. Four-way split

**A. Reusable vendor-neutral concepts/patterns** (keep, generalize, formalize as the adapter contract)
- Still-before-video approval gate (`SKILL.md` "influencer recreation" / "product showcase" two-step flows)
- Bounded QA retry loop (2 retries / 3 attempts total, on stills)
- Mandatory cost-estimate-then-confirm gate, with a documented "QA-fix retries skip re-confirmation but still bill" exception
- Mandatory dialogue-approval gate (separate from cost gate) for any video with speech
- Provenance logging pattern (`logs/*.jsonl`, append-at-request / update-at-completion, never logging secrets or full prompt text)
- Reference-image auto-upscale-to-1024px + RGB-JPEG normalization before submission
- Session-scoped dated output organization (generalizes past Arcads' folder/project API to "just use a dated local output directory" for any backend)
- "Never batch chat-pasted images — require files on disk" rule
- Safety suffixes pattern (`NO_CHROME_SUFFIX`, `SAFE_ZONE_SUFFIX`, `GLYPH_SAFETY_SUFFIX`) — these are prompt-engineering guards, not Arcads API mechanics; portable to any image backend
- Brand-contract-locked generator scripts (one script = one model family, refuses `--model` overrides) — good pattern to keep per-backend

**B. Reusable prompting/creative knowledge** (vendor-neutral content, keep as-is)
- `shared/skills/image-ad-prompting/prompting/prompt-library.md` (37 templates) + `template-format.md` + `safety-suffixes.md`
- `skills/arcads-external-api/prompting/prompt-library/*.md` — the per-model formulas (UGC, premium reveal, product hero, studio lookbook, feature walkthrough, character sheet, ugc-product-selfie) describe *what to say to a generative model*, not *how to call Arcads*. These formulas are directly reusable prompt text for OpenAI/Runway/etc., even though today's file also embeds Arcads field names in the "how to submit" sections.
- `shared/skills/pixar-style-ad/`, `shared/skills/claymation-ad/` guides — the cast-sheet, beat-structure, and continuity methodology is 90% vendor-neutral; only the "Per-API endpoint notes" tables are Arcads/KIE-specific
- `shared/skills/generate-youtube-thumbnail/prompting/{guide.md,formulas.md}` — likeness-lock and CTR formulas
- `shared/skills/caption-video/prompting/guide.md` — fully vendor-neutral already (no generative-API calls at all)
- `MASTER_CONTEXT.template.md` "Universal prompting principles" section — explicitly already labeled universal

**C. Arcads-specific execution code** (isolate behind the adapter, do not delete)
- `skills/chatgpt-image-ad/scripts/generate_image.py`, `skills/nano-banana-image-ad/scripts/generate_image.py`
- `skills/generate-youtube-thumbnail/scripts/generate-batch.sh`
- `skills/arcads-external-api/**` (SKILL.md, reference.md, all endpoint/model mechanics)
- `scripts/setup.sh`, `scripts/check-arcads-env.sh`
- `.env.example` Arcads block, `logs/arcads-api.jsonl` schema (Arcads-specific fields like `creditsCharged`)

**D. Promotional/community/nonessential material** (leave untouched; out of scope for a technical adaptation)
- README.md walkthrough video + Skool community sections, AGENTS.md/CLAUDE.md "surface the community" trigger rules, the `?via=claude-code` affiliate link, `shared/CLAUDE.md` community section

## 4. Proposed adapter design

### 4.1 Principle

Skills stay the interface the *agent* reads (SKILL.md prose). What changes is that the
"how do I actually generate this asset" step becomes a call through a small,
stdlib-only Python contract (`adapters/`) instead of a hardcoded `curl
external-api.arcads.ai` or a fixed script path. A skill's SKILL.md says "call the
adapter with these parameters"; the adapter decides which backend handles it based on
what's configured, and every backend implementation returns the same result shape so
downstream steps (QA, provenance logging, cost display) don't change per backend.

### 4.2 Interface (`adapters/base.py`)

```python
class CreativeBackend(ABC):
    name: str
    def capabilities(self) -> set[Capability]: ...          # {IMAGE, IMAGE_EDIT, VIDEO, VIDEO_EDIT}
    def check_credentials(self) -> CredentialStatus: ...     # never raises; ok/why-not
    def estimate_cost(self, request: GenerationRequest) -> CostEstimate | None: ...
    def generate(self, request: GenerationRequest) -> list[GenerationResult]: ...
```

`GenerationRequest` carries: kind, prompt, aspect_ratio, references (paths),
source (path, for edits), `product_lock` (see §4.4), n, output_dir. `GenerationResult`
carries: variant, path, backend name, model, cost estimate + currency (backend-native
unit — credits, USD, or `None` if unknown), and a `provenance` dict (backend, model,
timestamp, sha256 of inputs/outputs, prompt hash — never the raw prompt or credentials).

### 4.3 Cross-cutting policy (`adapters/policy.py`) — backend-independent, enforced once

- `RetryPolicy` — bounded QA-fix retries (default cap 2, matching the existing Arcads rule)
- `CostGate` — must be satisfied (explicit user confirmation) before first generation;
  QA-fix retries are exempt but still counted/logged
- `require_still_before_video(...)` — refuses to build a video request whose source
  frame lacks an approved still result
- `ProductFidelityGuard` — see §4.4
- `write_provenance(result, log_path)` — appends one JSON line per result, generalizing
  today's `logs/arcads-api.jsonl` convention to `logs/adapter-calls.jsonl` with a
  backend-agnostic schema (superset covers Arcads' `creditsCharged` as one possible
  `cost.amount` + `cost.unit="credits"`)

### 4.4 Product-fidelity hard rule

Per the user's requirement: when packaging/label/logo/SKU text must render exactly,
canonical product pixels must be preserved — never regenerated from a text prompt.

`GenerationRequest.product_lock: ProductLock | None` where:

```python
@dataclass
class ProductLock:
    canonical_image: Path       # the real product photo — pixels of the product must survive
    mode: Literal["environment-only"]  # only mode supported in phase 1
```

`ProductFidelityGuard.check(request)`:
- If `product_lock` is set, the request MUST be routed through a backend capability
  that supports `IMAGE_EDIT` (inpaint/compose around a fixed region), never plain
  `IMAGE` (full text-to-image regeneration). If the chosen backend/mode can't do that,
  the guard raises `ProductFidelityViolation` rather than silently falling back to
  full regeneration.
- This encodes exactly the rule already implicit in the Arcads skill's `image_edit`
  mode / `--source` flag (chatgpt-image-ad, nano-banana-image-ad already have this
  mode) — the guard just makes it mandatory-by-construction instead of
  optional-by-convention when a canonical product image exists in
  `references/products/`.

### 4.5 Backend registry (`adapters/registry.py`)

```python
BACKENDS: dict[str, type[CreativeBackend]] = {
    "arcads": ArcadsBackend,        # REAL — wraps the existing, tested generate_image.py scripts
    "openai": OpenAIBackend,        # STUB — capabilities declared, generate() raises NotImplementedError
    "runway": RunwayBackend,        # STUB
    "remotion": RemotionBackend,    # STUB
    "canva": CanvaBackend,          # STUB
    "descript": DescriptBackend,    # STUB
}
```

`ArcadsBackend` is a thin wrapper that shells out to the two existing
`generate_image.py` scripts (subprocess, same as a human would run them) — it does
**not** reimplement the Arcads HTTP calls, so there is exactly one place that owns
Arcads request-building (the existing scripts), and the adapter can't drift from it.

The five other backends are **honest stubs**: `check_credentials()` correctly reports
what env var is missing and that no execution path exists yet; `generate()` raises
`NotImplementedError` with a message naming the missing implementation. This is
intentional — this repo has no OpenAI/Runway/Remotion/Canva/Descript credentials or
tested request-building logic, and inventing one without the ability to test it against
real endpoints (no billable calls allowed) would violate "don't claim a backend is
automated unless there's a real executable path." Wiring each stub to a real,
tested implementation is future work, one backend at a time, each gated on the user
actually having that vendor's credentials to validate against.

## 5. Risks

1. **Two sources of truth during migration.** Until skills are updated to call the
   adapter instead of the hardcoded scripts directly, `adapters.ArcadsBackend` and a
   human/agent invoking `generate_image.py` directly are two paths to the same place.
   Mitigated by `ArcadsBackend` calling the *same* script rather than reimplementing it.
2. **Shared/ is upstream-owned.** Several comments in this repo state `shared/` is
   propagated from an external `gen-ai-core` repo by an out-of-repo `propagate.sh`.
   Editing `shared/skills/*` risks silent overwrite on the next upstream sync. The
   adapter layer therefore lives at the repo root (`adapters/`), not under `shared/`.
3. **Stub backends could be mistaken for working ones.** Mitigated by `NotImplementedError`
   at the one call site (`generate()`) plus `check_credentials()` reporting `ok=False`
   up front, plus this doc and the adapter README stating plainly that only Arcads has
   an executable path today.
4. **Cost model isn't backend-portable.** Arcads' "credits" have no fixed USD
   conversion; OpenAI/Runway are usually USD or token-metered. `CostEstimate` carries
   an explicit `unit` field and the cost gate always shows the unit — never silently
   normalizes currencies.
5. **Aspect ratio / duration / reference-count limits differ per vendor** (already true
   between Arcads' three image models, per `reference.md`). The adapter does not
   attempt a universal capability matrix in phase 1 — each backend's `capabilities()`
   and its own validation own that; cross-backend portability of a specific prompt
   template remains a per-template documentation exercise (as the image-ad-prompting
   library already does for gpt-image-2 vs nano-banana).
6. **No test coverage for the real Arcads path without a subscription.** `ArcadsBackend`
   is exercised in this phase only with argument-construction / dry-path unit tests
   (no network), consistent with "make no billable calls." End-to-end validation
   against a live Arcads account is deferred to whoever next has a key.

## 6. Acceptance criteria

**Phase 1 (this change):**
- [ ] `adapters/` package exists at repo root with `base.py`, `policy.py`,
      `registry.py`, one real backend (`arcads_backend.py`), five honest stub backends.
- [ ] No network calls anywhere in `adapters/` or its tests — verified by running the
      test suite offline.
- [ ] `ArcadsBackend` does not duplicate Arcads HTTP logic; it shells out to the
      existing `skills/*/scripts/generate_image.py` scripts unchanged.
- [ ] Product-fidelity guard is enforced in code (raises on violation), not just
      documented in prose.
- [ ] No existing file under `skills/` or `shared/` is modified or deleted — this phase
      is purely additive.
- [ ] Tests pass via `python3 -m unittest discover adapters/tests -v`.
- [ ] This doc + `adapters/README.md` explain, per backend, exactly what credential(s)
      would be needed and what remains unimplemented.

**Future phases (not in this change):**
- [x] At least one skill's SKILL.md updated to call `adapters.registry` instead of a
      hardcoded script path, with the Arcads path behaviorally unchanged end-to-end.
      — done in Phase 2 (`chatgpt-image-ad`).
- [ ] One additional real vendor backend (OpenAI is the natural next one — its
      `images/edits` endpoint maps directly onto the `IMAGE_EDIT` capability and the
      product-fidelity guard) implemented and validated by the user against their own
      credentials/spend. Not done — no credentials, no billable calls made.
- [x] `logs/adapter-calls.jsonl` schema adopted by at least one live skill run — done;
      see § 7.4 for a real local execution.

## 7. Phase 2 — one skill wired end-to-end, one real credential-free backend

Phase 2 answers the question Phase 1 left open: does the adapter contract actually
hold up when a real skill routes through it, and can useful work happen without any
vendor credentials at all? Two things were added to prove it, both real:

### 7.1 `chatgpt-image-ad` now routes through the adapter

`skills/chatgpt-image-ad/SKILL.md` Phase 5 was rewritten to invoke
`python3 -m adapters generate --backend arcads --model gpt-image-2 ...` instead of
calling `scripts/generate_image.py` directly. The script itself is **byte-for-byte
unchanged** — `adapters.ArcadsBackend` shells out to it exactly as it did in Phase 1.
The skill is not duplicated per vendor: the same command with `--backend
local_compositor` (or `compose`, its dedicated shorthand) reaches the second backend
below. This was the smallest change that satisfies "stop calling Arcads-specific
execution directly" without touching a single line of Arcads request-building logic.

### 7.2 `local_compositor` — a real, credential-free, deterministic backend

`adapters/local_compositor_backend.py` never calls a vendor. Given a canonical local
product image and a locally-supplied background (from anywhere — this skill's own
Arcads path with no product in the prompt, another tool entirely, or a photograph),
it deterministically composites them onto a standard social canvas (Pillow: resize
the background to cover, resize/paste the product, write PNG). This required adding
Pillow as this package's one non-stdlib dependency (`adapters/requirements.txt`) —
stdlib alone cannot decode/composite arbitrary JPEG inputs. Every other module in
`adapters/` remains stdlib-only.

Two placement modes: `native` (paste at original resolution — refuses to run at all
if the product doesn't fit, rather than silently downscaling it) is pixel-exact by
construction; `fit` (Lanczos-resize down to fit, never upscale) is documented and
reproducible but not pixel-exact, and says so in its own provenance record
(`product_pixels_exact: false`) — never conflated with an AI redraw.

### 7.3 Product-fidelity guard is now enforced at every real entry point, not just as a library call

Phase 1 shipped `ProductFidelityGuard.check()` as a function callers *could* invoke.
Phase 2 makes it structurally unavoidable: `ArcadsBackend.generate()`,
`UnimplementedBackend.generate()` (inherited by every stub), and
`LocalCompositorBackend.generate()` all call the guard themselves as the first thing
they do when `request.product_lock` is set — before checking credentials, before
building any request. The CLI (`adapters/cli.py`) also checks it explicitly at the
`generate`/`compose` entry points, for a clean exit code instead of a raised
exception reaching the terminal. This means a product-locked request cannot reach a
full-generation (`kind="image"`) path in *any* backend, current or future, even if a
caller forgets to check — proven by
`adapters/tests/test_product_fidelity_enforcement.py`, which calls `generate()`
directly (mocking only the subprocess boundary) and asserts `ProductFidelityViolation`
fires before anything else happens.

### 7.4 Real local run (no network, no vendor)

```bash
python3 -m adapters compose \
  --product references/products/048e6abb-00d5-4dae-820a-ad4769bc45d2.jpg \
  --background outputs/demo/background.png \
  --output outputs/demo/product-vertical.png \
  --aspect 9:16 --mode fit
```

Ran twice against identical inputs; outputs were byte-identical (`cmp` exit 0),
confirming determinism live, not just in a unit test. This also surfaced a real bug —
`compose` renamed the output file to `--output` *after* the generic
`build_provenance()` helper had already hashed it at its pre-rename path, so
`logs/adapter-calls.jsonl` recorded `output_sha256: null`. Fixed by recomputing the
hash against the final path before logging; regression-tested in
`adapters/tests/test_cli.py::test_real_local_run_produces_file_and_provenance`.

### 7.5 What Phase 2 deliberately does not do

- No OpenAI/Runway/Canva/Descript request-building — still honest stubs, per the task
  constraint. `local_compositor` is the only new *executable* backend.
- Arcads video models are still not wired into the adapter (unchanged from Phase 1
  scope) — `chatgpt-image-ad` only needed image/image_edit.
- No other skill (`nano-banana-image-ad`, `generate-youtube-thumbnail`,
  `image-ad-clone`, `arcads-external-api` itself) was touched — they call their
  scripts directly exactly as before Phase 2.

## 8. Phase 3 — one complete terminal-first production job, manifest to approval package

Phase 2 proved a single generation call could route through the adapter. Phase 3
proves the *job* — the unit of work a human actually approves — end to end: a
manifest describes intent, the runner validates it, enforces fidelity, generates,
QA's the result against reality (not just against itself), records provenance, and
produces a package a human can review without re-deriving anything.

### 8.1 Job manifest (`adapters/job.py`)

`CreativeJob` is a thin wrapper, not a new execution contract — `to_generation_request()`
builds the exact same `GenerationRequest` every other adapter path already uses. It adds
what a `GenerationRequest` alone doesn't carry: a stable `job_id`, `channel`,
`output_dir` as a job-level concept (not just an implementation detail of one call),
and a `provenance_seed` (`requested_by`/`notes`) that gets folded into the output
provenance record — see § 3 (Reusable vendor-neutral concepts) "provenance logging
pattern," now with human-facing who/why context, not just machine facts.

`product.fidelity` is a required field (`"environment-only"` or the explicit opt-out
`"none"`) — the manifest format makes the fidelity decision visible and reviewable
before a job ever runs, rather than something buried in a backend call.

### 8.2 Job runner (`adapters/job_runner.py`) and its Phase 3 boundary

`run_job()` is deliberately narrow: `SUPPORTED_BACKENDS_FOR_RUN_JOB = {"local_compositor"}`.
Any manifest naming a different backend — `"arcads"` included — is refused before the
registry is even consulted, let alone credentials checked or a subprocess spawned.
This isn't a workaround for missing Arcads credentials; it's the explicit Phase 3
constraint ("do not wire any network backend in this phase") enforced in code, not
just in this document. `adapters/tests/test_job_runner.py::TestDeliberateUnsafeBackendFailure`
proves it by mocking the subprocess boundary and asserting zero calls.

A second, independent layer of protection exists beneath the allowlist:
`ProductFidelityGuard.check()` still runs whenever a job carries a `ProductLock`,
so if a future phase widens the allowlist to include a backend that lacks
`IMAGE_EDIT`, a product-locked job is still refused — proven in
`TestGuardStillProtectsBeyondTheAllowlist` by temporarily monkeypatching the
allowlist to admit a hypothetical unsafe backend and confirming the guard, not the
allowlist, is what stops it.

Every failure — missing product, missing background, unsupported aspect ratio,
disallowed backend, fidelity violation — still writes an `approval-manifest.json`
documenting the reason. Nothing fails silently; a FAIL is exactly as reviewable as a PASS.

### 8.3 QA (`adapters/qa.py`) — deterministic, not heuristic

All 6 required checks are either plain file/metadata assertions or an independently
recomputed deterministic transform compared byte-for-byte against the real output —
no ML, no OCR, no network. The "no unexpected text overlay" requirement in particular
is satisfied structurally: `local_compositor` never calls a text/draw API at all (see
`adapters/local_compositor_backend.py` — resize/crop/paste only), and the QA check
proves that end-to-end by recomputing the background-only composite independently
(`resize_cover`, the same function the backend itself uses) and diffing it against the
actual output everywhere outside the product's placement box. A pixel mismatch there
means *something* was drawn onto the canvas beyond background+product — caught
regardless of what that something was.

### 8.4 Real production run

```bash
python3 -m adapters run-job jobs/soulcraft-maca-matcha.json
```

Ran twice, live, against the real file
`references/products/048e6abb-00d5-4dae-820a-ad4769bc45d2.jpg` (the same product used
in the Phase 2 `compose` demo) — both runs: `PASS`, 6/6 QA checks, identical
`output_sha256` across both runs (and identical to the Phase 2 `compose` demo output,
since the underlying inputs and params are the same — a nice cross-phase confirmation
of determinism). A deliberate `--backend arcads` variant of the same job was also run
live and confirmed `FAIL` (exit code 1) with zero subprocess/network calls, matching
the automated regression test. Evidence preserved under `outputs/jobs/` (gitignored).

### 8.5 What Phase 3 deliberately does not do

- No OpenAI/Runway/Canva/Descript/Arcads request-building added to `run-job` — only
  `local_compositor` executes.
- No masked/region-level `ProductLock` (still whole-canonical-image preservation,
  `mode="environment-only"` only — documented as a gap since Phase 1,
  `docs/adapter/PRODUCT_FIDELITY.md`).
- No batch/multi-output job support — a job manifest produces exactly one asset
  (`n=1`); batching is a natural but unimplemented extension.
- No publish/distribution step — `run_job()` stops at the approval package, by design.

### 8.6 Gaps before a real generative backend can be plugged in

- A real vendor backend (OpenAI is still the natural first pick — see § 6) needs to be
  implemented, credentialed, and validated by the user before `SUPPORTED_BACKENDS_FOR_RUN_JOB`
  should grow to include it. When it does, the QA suite needs a generative-specific
  addition: `no_unexpected_overlay`'s pixel-diff approach only works because
  `local_compositor` is provably non-generative; a generative backend's QA needs the
  visual-inspection step already documented in `skills/arcads-external-api/SKILL.md`
  ("Generated image QA") — a human/agent look, not a pixel diff.
- The job manifest schema has no versioning field yet — fine for one schema, but worth
  adding (`"schema_version": 1`) before a second, incompatible revision happens.
- `run-job` always uses `n=1`; multi-variant job manifests (e.g. "give me 3 crops of
  this campaign") aren't supported and would need either a `variants` array in the
  manifest or a loop at the CLI layer.

## 9. Independent-review fixes (commit 9da30e5 findings)

An independent review of Phase 3 (commit `9da30e5`) found two reproducible blocking
defects and several non-blocking hardening gaps. Fixes:

**B1 — manifest path containment (`adapters/job.py`).** `load_job()` previously
joined manifest-supplied paths onto `repo_root` with no normalization or containment
check; a manifest with `output_dir: "../../../../tmp/x"` wrote real files outside the
repo. `_resolve_contained()` now: rejects absolute manifest paths outright (portable
jobs shouldn't reference an operator's local filesystem layout); fully canonicalizes
relative paths with `.resolve()` (normalizes `..`, follows symlinks); and rejects
(never silently re-roots) anything that resolves outside `repo_root`. Applied to
`product.asset`, `background.asset`, and `output_dir` — the only three
manifest-derived filesystem paths that exist today. A rejected manifest raises before
`run_job()` is ever called, so no filesystem artifact — inside or outside the repo —
is created at all.

**B2 — crash-safe approval state (`adapters/job_runner.py`, `adapters/atomic.py`).**
`run_job()` had no exception containment around QA/provenance/manifest-write, and no
atomicity — an exception in that window could leave a stale `PASS` describing an
image the current run never validated (reproduced by the review). Fixed with an
explicit `RUNNING -> PASS | FAIL` lifecycle: any prior terminal state is overwritten
with `RUNNING` (atomically, via `adapters/atomic.py`'s temp-file-plus-`os.replace`)
*before* `generate()` runs, and every subsequent stage (generate, QA, provenance,
final manifest write) is individually wrapped so an exception there durably records
`FAIL` instead of leaving the previous state in place. The `JobResult` returned to
the caller is always in sync with what's on disk — a caller is only told `PASS` if
`approval-manifest.json` was atomically written with status `PASS`; if that specific
write fails, the result is `FAIL` even though QA passed, because nothing durable
says otherwise.

**N1 — `ProductFidelityGuard` source identity (`adapters/policy.py`).** The guard
checked that a locked file exists and the routing is capability-appropriate, but
never that `product_lock.canonical_image` is the same file as `request.source` — the
review constructed a request where they diverged and the guard passed. Now compares
resolved identity and raises `ProductFidelityViolation` on mismatch.

**N2 — provenance/log hygiene.** `logs/adapter-calls.jsonl` had accumulated
leftover test-pollution entries (from before an earlier fix) and, in the `run-job`
path, absolute local-machine paths (leaking the operator's home directory) instead
of the repo-relative convention every other log in this repo uses. Cleaned the
committed log to its one legitimate entry; `job_runner.py` now records
repo-relative paths (`_display_path()`) in `provenance.json`/`approval-manifest.json`/
the shared log wherever the path is actually under the repo (CLI-driven `compose`/
`generate` paths, which can legitimately point anywhere the operator chooses, are
unaffected). `jobs/soulcraft-maca-matcha.json`'s example `requested_by` was changed
from a real address to the placeholder `adapters/README.md` already documents.
`adapters/tests/test_log_hygiene.py` adds a regression guard that spawns the full
suite as a child process and asserts the committed log's hash is unchanged.

**N4 — `ArcadsBackend.generate()` happy-path coverage.** Every existing test that
touched `subprocess.run` only asserted it was *not* called (guard-rejection paths);
the actual argv-construction and stdout-parsing logic — the whole reason this backend
exists — had no direct coverage.
`adapters/tests/test_arcads_backend.py::TestGenerateHappyPathMocked` mocks
`subprocess.run` with realistic stdout and asserts argv shape (script selection,
flags, values), multi-variant/reference handling, and the returncode 2 / empty-stdout-1
/ partial-stdout-1 / malformed-JSON behaviors. No real Arcads call.
