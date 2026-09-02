#!/usr/bin/env python3
"""
rin_parser.py

Baseline reader for DDEX RIN (Recording Information Notification) XML
documents.

Scope (small baseline, not full DDEX RIN compliance -- see parser/README.md):
- Extracts sessions, contributors, equipment, and recording components,
  using well-established public DDEX RIN vocabulary (Session, Party,
  Equipment, RecordingComponent). These are genuine DDEX RIN concepts, not
  guessed names -- but the exact element structure has not been verified
  against a real DDEX RIN sample or XSD (none available in this repo at
  implementation time), so treat this as a best-effort baseline to refine
  once a real sample is available.
- Element matching is namespace-agnostic (see common/xml_utils.py) since
  DDEX namespace URIs vary by RIN version and no XSD is in-repo to pin one
  down.
- Missing/optional data is always left as None or an empty list -- never
  invented. A `warnings` list on the result flags things like "no <Session>
  elements found" or malformed XML, so a mismatch is visible rather than
  silently producing empty output.
- Does NOT implement scoring, validation-rule evaluation, or normalization
  into the Evidence Bundle format -- that is separate, future work.

Usage
-----
python parser/rin_parser.py path/to/rin_document.xml
    (or, from within parser/:  python rin_parser.py path/to/rin_document.xml)
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import List, Optional

from common.xml_utils import first_child_text, iter_by_local_name


@dataclass
class RinContributor:
    party_id: Optional[str] = None
    name: Optional[str] = None
    role: Optional[str] = None


@dataclass
class RinEquipment:
    equipment_id: Optional[str] = None
    description: Optional[str] = None
    equipment_type: Optional[str] = None


@dataclass
class RinSession:
    session_id: Optional[str] = None
    date: Optional[str] = None
    location: Optional[str] = None
    contributors: List[RinContributor] = field(default_factory=list)
    equipment: List[RinEquipment] = field(default_factory=list)


@dataclass
class RinRecordingComponent:
    component_id: Optional[str] = None
    title: Optional[str] = None
    role: Optional[str] = None


@dataclass
class RinParseResult:
    source_path: str
    sessions: List[RinSession] = field(default_factory=list)
    contributors: List[RinContributor] = field(default_factory=list)
    equipment: List[RinEquipment] = field(default_factory=list)
    recording_components: List[RinRecordingComponent] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def _parse_contributor(elem: ET.Element) -> RinContributor:
    return RinContributor(
        party_id=first_child_text(elem, "PartyId"),
        name=first_child_text(elem, "Name") or first_child_text(elem, "FullName"),
        role=first_child_text(elem, "Role"),
    )


def _parse_equipment(elem: ET.Element) -> RinEquipment:
    return RinEquipment(
        equipment_id=first_child_text(elem, "EquipmentId"),
        description=first_child_text(elem, "Description"),
        equipment_type=first_child_text(elem, "EquipmentType") or first_child_text(elem, "Type"),
    )


def _parse_session(elem: ET.Element) -> RinSession:
    return RinSession(
        session_id=first_child_text(elem, "SessionId"),
        date=first_child_text(elem, "Date"),
        location=first_child_text(elem, "Location"),
        contributors=[_parse_contributor(p) for p in iter_by_local_name(elem, "Party")],
        equipment=[_parse_equipment(e) for e in iter_by_local_name(elem, "Equipment")],
    )


def _parse_recording_component(elem: ET.Element) -> RinRecordingComponent:
    return RinRecordingComponent(
        component_id=first_child_text(elem, "ComponentId"),
        title=first_child_text(elem, "Title"),
        role=first_child_text(elem, "Role"),
    )


def parse_rin_file(path: str) -> RinParseResult:
    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        return RinParseResult(source_path=path, warnings=[f"malformed XML: {exc}"])
    except OSError as exc:
        return RinParseResult(source_path=path, warnings=[f"could not read file: {exc}"])

    root = tree.getroot()
    warnings: List[str] = []

    session_elems = list(iter_by_local_name(root, "Session"))
    if not session_elems:
        warnings.append("no <Session> elements found")
    sessions = [_parse_session(e) for e in session_elems]

    component_elems = list(iter_by_local_name(root, "RecordingComponent"))
    if not component_elems:
        warnings.append("no <RecordingComponent> elements found")
    recording_components = [_parse_recording_component(e) for e in component_elems]

    return RinParseResult(
        source_path=path,
        sessions=sessions,
        contributors=[c for s in sessions for c in s.contributors],
        equipment=[e for s in sessions for e in s.equipment],
        recording_components=recording_components,
        warnings=warnings,
    )


def main() -> int:
    arg_parser = argparse.ArgumentParser(
        description="Parse a DDEX RIN XML file into baseline session/contributor/equipment/recording-component data."
    )
    arg_parser.add_argument("path", help="Path to a DDEX RIN XML file")
    args = arg_parser.parse_args()

    result = parse_rin_file(args.path)
    print(json.dumps(dataclasses.asdict(result), indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
