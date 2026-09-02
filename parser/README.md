# Provenance parsers: C2PA / DDEX RIN / DDEX ERN

Small baseline for extracting raw, standard-specific data from three
provenance-related formats. This is deliberately **not** a complete
implementation of any of the three standards, and it does **not** score,
validate, detect AI, or normalize anything into the team's Evidence Bundle
format — see [Out of scope](#out-of-scope) below.

## What's implemented

- **`c2pa_parser.py`** — reads an embedded C2PA manifest (if present) from a
  media file via the official `c2pa-python` bindings: presence, active
  manifest label, assertions, ingredients, signature info, and raw
  validation status.
- **`rin_parser.py`** — reads a DDEX RIN XML document: sessions,
  contributors, equipment, and recording components.
- **`ern_parser.py`** — reads a DDEX ERN XML document: baseline release
  metadata (id/title/type). **Does not** extract AI-declaration information
  — see [Limitations](#limitations).

Each parser is standalone: it reads its own standard and emits its own JSON
shape (via a `@dataclass` result + `dataclasses.asdict`). None of them read
or depend on the root `c2pa.json` / `evidence_bundle_schema.json` /
`evidence_profile.json` files — those are a separate scoring rubric and
target output shape for a future normalization layer, not consumed here.
Field-group names (e.g. `sessions`/`contributors`/`equipment`/
`recording_components`) were chosen to line up with that eventual target
shape, purely to make a future normalizer's job easier — but the shapes
below are not that format.

## Installation

From the repo root:

```bash
cd parser
pip install -e ".[dev]"          # RIN/ERN + tests (pytest) — no native deps
pip install -e ".[c2pa,dev]"     # also installs c2pa-python for the C2PA parser
```

`c2pa-python` is an **optional** dependency (Python ≥3.10, native binding)
kept out of the base install so RIN/ERN work needs nothing beyond the
standard library. If it isn't installed, `c2pa_parser.py` still runs and
reports a clear "not installed" message instead of crashing.

## CLI usage

Run each script directly (no install required for RIN/ERN):

```bash
python parser/rin_parser.py parser/tests/fixtures/rin_minimal.xml
python parser/ern_parser.py parser/tests/fixtures/ern_minimal.xml
python parser/c2pa_parser.py path/to/some_file.jpg
```

Each prints its result as indented JSON.

## Out of scope

Explicitly not implemented in this baseline:
- Scoring, or Completeness / Integrity / Attestation Strength calculation
- AI detection (as opposed to reading an explicit declaration, once one can
  be reliably located)
- Cross-parser normalization into the Evidence Bundle format
- Full DDEX RIN or ERN spec coverage
- Reading, validating against, or modifying the root Evidence Bundle
  Schema / Evidence Profile / `c2pa.json` files

## Limitations

- **RIN element names are grounded in standard public DDEX RIN vocabulary
  (`Session`, `Party`, `Equipment`, `RecordingComponent`, ...) but have not
  been verified against a real DDEX RIN sample or XSD** — none was
  available in this repo at implementation time. Matching is
  namespace-agnostic (see `common/xml_utils.py`) to reduce the risk of
  silently matching nothing due to a namespace-version mismatch, and the
  parser emits a `warnings` entry whenever an expected top-level element
  (e.g. `<Session>`, `<RecordingComponent>`) isn't found at all.
- **`ern_parser.py` does not extract AI-declaration information.** DDEX's
  AI-disclosure extension is newer and less documented than the rest of
  ERN, and no authoritative element name could be confirmed from a real
  sample, XSD, or official DDEX reference. Rather than guess (e.g. matching
  any tag containing `"ai"`), `contains_ai_declared` stays `None` and
  `ai_contributions` stays `[]` always, with a fixed `warnings` entry
  explaining why. This is the single highest-priority item to revisit once
  a real ERN sample/XSD (with confirmed AI-disclosure element names) is
  available.
- **Test fixtures under `tests/fixtures/*.xml` are hand-written synthetic
  files for exercising parser behaviour only** (successful parsing,
  missing-field handling, malformed-XML handling) — they are not DDEX
  compliance tests and don't prove the parser matches real-world documents.
- **No C2PA test fixture is bundled.** Sourcing a real signed test asset
  was kept out of scope for this pass; `test_c2pa_parser.py` only covers
  the "file not found" and "c2pa-python not installed" code paths.
- **`c2pa_parser.py`'s manifest-JSON field reads (`assertions`,
  `ingredients`, `signature_info`, `validation_status`) are a best-effort,
  defensive (`.get()`-based) pass-through** of the manifest store JSON
  returned by `c2pa.Reader(path).json()` — the exact field names were not
  independently re-verified against every possible manifest producer, so
  an unrecognized manifest shape degrades to `None`/`[]` fields rather than
  an error, not a guarantee of complete extraction.

## How to extend

- Once a real DDEX RIN sample/XSD is available: verify/adjust the element
  names targeted in `rin_parser.py`'s `_parse_session` /
  `_parse_contributor` / `_parse_equipment` / `_parse_recording_component`,
  and add fixtures under `tests/fixtures/` built from (or close to) the
  real structure.
- Once a real DDEX ERN sample/XSD with AI-disclosure content is available:
  add the confirmed element name(s) to `ern_parser.py`'s `_parse_release`
  area (or a new `_parse_ai_declaration` helper) and remove the
  `AI_DECLARATION_LIMITATION` warning once implemented.
- New shared XML helpers only belong in `common/xml_utils.py` if both
  `rin_parser.py` and `ern_parser.py` need them — keep parser-specific
  logic in the parser file itself.
- The eventual normalization step (reshaping these parsers' output into
  `evidence_bundle_schema.json`'s `provenance.c2pa` / `extension.audio.rin`
  / `extension.audio.ern` shapes) is intentionally not started here — it
  belongs in a separate module once the team's schema is finalized.

## Testing

```bash
cd parser
pytest                    # after installing with pip install -e ".[dev]"
```

or, without installing, from the repo root:

```bash
pytest parser/tests
```

C2PA tests that require `c2pa-python` to be absent are skipped automatically
when it's installed (and vice versa) via `pytest.mark.skipif`.
