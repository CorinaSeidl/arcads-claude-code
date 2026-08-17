# Hard rule: product-fidelity

**When packaging, labels, logos, or SKU text must be exact, never regenerate the
product from a text prompt. Preserve the canonical product pixels and
generate/edit only the environment around them.**

## Why this is a hard rule, not a style preference

Generative image models cannot reliably reproduce small dense text (label copy,
ingredient lists, barcodes, exact logo geometry) from a description — this is already
documented as a known failure mode in this repo (`shared/skills/image-ad-prompting/
prompting/prompt-library.md`'s `GLYPH_SAFETY_SUFFIX`, and the QA checklist in
`skills/arcads-external-api/SKILL.md` calling out "unreadable or garbled text" as a
defect to catch). For real product photography where the label IS the product's legal
and brand identity, "close enough" text is not an acceptable QA outcome — it must be
the real pixels, not a redraw.

## The rule, precisely

1. If a canonical product image exists (typically under `references/products/`) and
   the output needs the product's packaging/label/logo/SKU text to be legible and
   correct, the generation request MUST:
   - Use an image-edit / inpaint capability, with the canonical image as the edit
     `source` — never a plain text-to-image call that merely *describes* the product.
   - Constrain the edit to the environment/background/composition around the product,
     not the product's own surface.
2. If no image-edit-capable backend is available for the chosen model, **stop and say
   so** — do not fall back to full regeneration and hope the text comes out legible.
3. This is enforced in code by `adapters.policy.ProductFidelityGuard` (see
   `docs/adapter/DESIGN.md` § 4.4): a `GenerationRequest` carrying a `ProductLock`
   that isn't routed through an `IMAGE_EDIT`-capable backend with `kind="image_edit"`
   raises `ProductFidelityViolation` rather than proceeding.

## What this does NOT cover yet

- Video: `ProductLock` on a `kind="video"` request is rejected in phase 1
  (`GenerationRequest.__post_init__`). The pattern for video is: produce the
  product-locked still first (per this rule), get it approved, then animate it as
  `start_frame` — i.e., compose with the still-before-video gate
  (`adapters.policy.require_still_before_video`), not by asking a video model to draw
  the product itself.
- Partial-region locking (e.g. "preserve only the label, allow repositioning the
  bottle") — phase 1 only supports whole-canonical-image preservation
  (`mode="environment-only"`). A masked/region variant is future work.

## Where this connects to existing repo conventions

- `skills/chatgpt-image-ad/scripts/generate_image.py` and
  `skills/nano-banana-image-ad/scripts/generate_image.py` already have an
  `image_edit` mode (`--mode image_edit --source <path>`) — this rule makes using it
  mandatory-by-construction (via the adapter guard) whenever a canonical product image
  is in play, instead of optional-by-convention.
- `MASTER_CONTEXT.template.md`'s "Universal prompting principles" is the right place
  for a human-facing summary of this rule once a project's default product image is
  known; this file is the technical specification the guard code implements.
