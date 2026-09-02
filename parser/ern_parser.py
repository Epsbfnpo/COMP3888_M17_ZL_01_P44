#!/usr/bin/env python3
"""
ern_parser.py

Baseline reader for DDEX ERN (Electronic Release Notification) XML
documents.

Scope (small baseline, not full DDEX ERN compliance -- see parser/README.md):
- Extracts baseline release metadata (release id, title, release type) using
  well-established public DDEX ERN vocabulary (Release, TitleText,
  ReleaseType). Element matching is namespace-agnostic (see
  common/xml_utils.py) since DDEX namespace URIs vary by ERN version and no
  XSD is in-repo to pin one down.
- Deliberately does NOT extract declared AI-involvement/AI-contribution
  information in this baseline. DDEX's AI-disclosure extension is newer and
  less documented than the rest of ERN, and no authoritative element name
  could be confirmed from a real sample, XSD, or official DDEX reference at
  implementation time. Rather than guess via substring matching (e.g. any
  tag containing "ai"), `contains_ai_declared` and `ai_contributions` are
  always left None/empty, with a fixed warning explaining why. This is a
  deliberate limitation to revisit once a real ERN sample/XSD is available
  -- see parser/README.md.
- Missing/optional data is always left as None or an empty list -- never
  invented.
- Does NOT implement scoring, validation-rule evaluation, AI detection, or
  normalization into the Evidence Bundle format -- that is separate, future
  work. This parser reports what a file *declares*, nothing more.

Usage
-----
    python parser/ern_parser.py path/to/ern_document.xml
    (or, from within parser/:  python ern_parser.py path/to/ern_document.xml)
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, List, Optional

from common.xml_utils import first_child_text, iter_by_local_name

AI_DECLARATION_LIMITATION = (
    "AI-declaration extraction is unsupported in this baseline: no authoritative "
    "DDEX ERN AI-disclosure element name could be confirmed from a real sample, "
    "XSD, or official DDEX reference. contains_ai_declared and ai_contributions "
    "are always left None/empty rather than guessed. See parser/README.md."
)


@dataclass
class ErnRelease:
    release_id: Optional[str] = None
    title: Optional[str] = None
    release_type: Optional[str] = None


@dataclass
class ErnParseResult:
    source_path: str
    releases: List[ErnRelease] = field(default_factory=list)
    contains_ai_declared: Optional[bool] = None
    ai_contributions: List[Any] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def _parse_release(elem: ET.Element) -> ErnRelease:
    return ErnRelease(
        release_id=first_child_text(elem, "ReleaseId") or first_child_text(elem, "ReleaseReference"),
        title=first_child_text(elem, "TitleText") or first_child_text(elem, "Title"),
        release_type=first_child_text(elem, "ReleaseType"),
    )


def parse_ern_file(path: str) -> ErnParseResult:
    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        return ErnParseResult(source_path=path, warnings=[f"malformed XML: {exc}", AI_DECLARATION_LIMITATION])
    except OSError as exc:
        return ErnParseResult(source_path=path, warnings=[f"could not read file: {exc}", AI_DECLARATION_LIMITATION])

    root = tree.getroot()
    warnings: List[str] = [AI_DECLARATION_LIMITATION]

    release_elems = list(iter_by_local_name(root, "Release"))
    if not release_elems:
        warnings.append("no <Release> elements found")
    releases = [_parse_release(e) for e in release_elems]

    return ErnParseResult(
        source_path=path,
        releases=releases,
        contains_ai_declared=None,
        ai_contributions=[],
        warnings=warnings,
    )


def main() -> int:
    arg_parser = argparse.ArgumentParser(
        description="Parse a DDEX ERN XML file into baseline release metadata. Does not extract AI-declaration info (see module docstring)."
    )
    arg_parser.add_argument("path", help="Path to a DDEX ERN XML file")
    args = arg_parser.parse_args()

    result = parse_ern_file(args.path)
    print(json.dumps(dataclasses.asdict(result), indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
