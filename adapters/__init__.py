"""Vendor-neutral creative-backend adapter layer.

See docs/adapter/DESIGN.md for the full architecture and dependency audit this
package implements. Stdlib only — no third-party dependencies, matching the
convention already used by skills/*/scripts/generate_image.py.

Only ``ArcadsBackend`` has a real, executable request path (it wraps the existing,
tested generate_image.py scripts). Every other backend registered here is an honest
stub: capabilities are declared, credential checks report what's missing, and
``generate()`` raises NotImplementedError rather than pretending to call an API this
repo has never exercised. See adapters/README.md before wiring a new backend for real.
"""
