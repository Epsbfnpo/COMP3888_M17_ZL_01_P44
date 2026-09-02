#!/usr/bin/env python3
"""
c2pa_parser.py

Baseline reader for C2PA (Coalition for Content Provenance and Authenticity)
manifest data, using the official `c2pa-python` bindings.

Scope (small baseline -- see parser/README.md):
- Detects whether a C2PA manifest is present and reads it via the official
  Reader API. Verified usage (from the c2pa-python project's own README and
  examples): construct with `c2pa.Reader(path)` as a context manager, call
  `.json()` to get the manifest store as a JSON string. This baseline does
  NOT call `get_active_manifest()` or a `get_validation_state()`-style
  method, since their exact return shapes were not independently verified
  in this session -- instead it reads `active_manifest` / `manifests` /
  `validation_status` directly from the manifest store JSON dict via
  `.get()`, so a field that isn't present simply stays None rather than
  raising or being guessed.
- Extracts assertions, ingredients, and signature info as a largely raw
  pass-through of whatever the manifest JSON contains for those keys.
  Every field is read with `.get()` and defaults to None/[] -- this is not
  a verified guarantee that the field names match every manifest producer's
  output, just a best-effort baseline extraction.
- Does NOT re-evaluate, score, or trust-judge validation results --
  `validation_status` is passed through as-is, unexamined.
- `c2pa-python` is an OPTIONAL dependency (see parser/pyproject.toml's
  `c2pa` extra). If it isn't installed, parsing degrades to a clear,
  actionable warning instead of a crash, so rin_parser.py/ern_parser.py
  remain usable standalone.
- No C2PA test fixture is bundled with this baseline -- see parser/README.md
  limitations.

Usage
-----
    python parser/c2pa_parser.py path/to/file.jpg
    (or, from within parser/:  python c2pa_parser.py path/to/file.jpg)
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from dataclasses import dataclass, field
from typing import Any, List, Optional

try:
    import c2pa  # type: ignore
except ImportError:
    c2pa = None  # optional dependency; see module docstring


@dataclass
class C2paAssertion:
    label: Optional[str] = None
    data: Any = None


@dataclass
class C2paIngredient:
    title: Optional[str] = None
    relationship: Optional[str] = None
    format: Optional[str] = None


@dataclass
class C2paSignatureInfo:
    issuer: Optional[str] = None
    time: Optional[str] = None
    alg: Optional[str] = None


@dataclass
class C2paParseResult:
    source_path: str
    present: bool = False
    active_manifest_label: Optional[str] = None
    assertions: List[C2paAssertion] = field(default_factory=list)
    ingredients: List[C2paIngredient] = field(default_factory=list)
    signature_info: Optional[C2paSignatureInfo] = None
    validation_status: Any = None
    warnings: List[str] = field(default_factory=list)


def _parse_assertion(raw: dict) -> C2paAssertion:
    return C2paAssertion(label=raw.get("label"), data=raw.get("data"))


def _parse_ingredient(raw: dict) -> C2paIngredient:
    return C2paIngredient(
        title=raw.get("title"),
        relationship=raw.get("relationship"),
        format=raw.get("format"),
    )


def _parse_signature_info(raw: Optional[dict]) -> Optional[C2paSignatureInfo]:
    if not raw:
        return None
    return C2paSignatureInfo(issuer=raw.get("issuer"), time=raw.get("time"), alg=raw.get("alg"))


def parse_c2pa_file(path: str) -> C2paParseResult:
    """Read whatever C2PA manifest data is present in `path`, if any.

    A file with no embedded manifest is the common case, not an error -- it
    is reported as present=False with a warning, never raised to the caller.
    """
    if c2pa is None:
        return C2paParseResult(
            source_path=path,
            warnings=[
                "c2pa-python is not installed; install the optional extra with "
                'pip install -e "parser[c2pa]" to enable C2PA parsing.'
            ],
        )

    try:
        with c2pa.Reader(path) as reader:
            store = json.loads(reader.json())
    except Exception as exc:
        # Mirrors the c2pa-python project's own example, which wraps Reader
        # construction/reading in a broad except -- "no manifest present"
        # and genuine read errors aren't reliably distinguishable via a
        # documented exception type at this baseline, so both are reported
        # here as present=False with the underlying message preserved.
        return C2paParseResult(source_path=path, present=False, warnings=[f"no C2PA manifest read: {exc}"])

    active_label = store.get("active_manifest")
    manifests = store.get("manifests") or {}
    manifest = manifests.get(active_label) if active_label else None

    if manifest is None:
        return C2paParseResult(
            source_path=path,
            present=False,
            validation_status=store.get("validation_status"),
            warnings=["manifest store read but no active manifest found"],
        )

    return C2paParseResult(
        source_path=path,
        present=True,
        active_manifest_label=active_label,
        assertions=[_parse_assertion(a) for a in manifest.get("assertions") or []],
        ingredients=[_parse_ingredient(i) for i in manifest.get("ingredients") or []],
        signature_info=_parse_signature_info(manifest.get("signature_info")),
        validation_status=store.get("validation_status"),
    )


def main() -> int:
    arg_parser = argparse.ArgumentParser(
        description="Read baseline C2PA manifest data (assertions/ingredients/signature info) from a file."
    )
    arg_parser.add_argument("path", help="Path to a media file that may contain an embedded C2PA manifest")
    args = arg_parser.parse_args()

    result = parse_c2pa_file(args.path)
    print(json.dumps(dataclasses.asdict(result), indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
