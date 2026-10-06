#!/usr/bin/env python3
"""Bounded DDEX RIN 2.1 parser with offline XSD validation and file binding."""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, List, Optional

try:
    from .common.ddex_validation import namespace_of, validate_ddex
    from .common.xml_utils import first_child_text, iter_by_local_name, parse_bounded_xml
except ImportError:
    from common.ddex_validation import namespace_of, validate_ddex
    from common.xml_utils import first_child_text, iter_by_local_name, parse_bounded_xml

SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


def _first(elem, name):
    return next(iter_by_local_name(elem, name), None)


def _text(elem):
    value = "".join(elem.itertext()).strip() if elem is not None else ""
    return value or None


def _descendant_text(elem, *names):
    for name in names:
        value = _text(_first(elem, name))
        if value:
            return value
    return None


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
class RinFile:
    file_reference: Optional[str] = None
    uri: Optional[str] = None
    sha256: Optional[str] = None
    hash_algorithm: Optional[str] = None
    size_bytes: Optional[int] = None
    bit_depth: Optional[int] = None
    sample_rate_hz: Optional[float] = None


@dataclass
class RinRecordingComponent:
    component_id: Optional[str] = None
    title: Optional[str] = None
    role: Optional[str] = None
    file_reference: Optional[str] = None


@dataclass
class RinSoundRecording:
    resource_reference: Optional[str] = None
    isrc: Optional[str] = None
    title: Optional[str] = None
    file_reference: Optional[str] = None


@dataclass
class RinParseResult:
    source_path: str
    namespace: Optional[str] = None
    schema_version: Optional[str] = None
    xsd_validation: dict[str, Any] = field(default_factory=dict)
    sessions: List[RinSession] = field(default_factory=list)
    contributors: List[RinContributor] = field(default_factory=list)
    equipment: List[RinEquipment] = field(default_factory=list)
    recording_components: List[RinRecordingComponent] = field(default_factory=list)
    sound_recordings: List[RinSoundRecording] = field(default_factory=list)
    files: List[RinFile] = field(default_factory=list)
    audio_bindings: List[dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def _parse_contributor(elem):
    return RinContributor(_descendant_text(elem, "PartyReference", "PartyId"),
                          _descendant_text(elem, "FullName", "Name"),
                          _descendant_text(elem, "Role"))


def _parse_equipment(elem):
    return RinEquipment(_descendant_text(elem, "EquipmentReference", "EquipmentId"),
                        _descendant_text(elem, "Description"),
                        _descendant_text(elem, "EquipmentType", "Type"))


def _parse_session(elem):
    return RinSession(_descendant_text(elem, "SessionReference", "SessionId"),
                      _descendant_text(elem, "StartDate", "Date"),
                      _descendant_text(elem, "VenueName", "Location"),
                      [_parse_contributor(item) for item in iter_by_local_name(elem, "Party")],
                      [_parse_equipment(item) for item in iter_by_local_name(elem, "Equipment")])


def _parse_file(elem):
    hash_sum = _first(elem, "HashSum")
    hash_value = _descendant_text(hash_sum, "HashSumValue") if hash_sum is not None else None
    algorithm = (_descendant_text(hash_sum, "HashSumAlgorithmType") or
                 _descendant_text(hash_sum, "Algorithm")) if hash_sum is not None else None
    sha256 = hash_value.lower() if hash_value and SHA256.fullmatch(hash_value) and (
        not algorithm or "sha" in algorithm.casefold() and "256" in algorithm) else None
    def number(name, converter, multiplier=1):
        try:
            value = first_child_text(elem, name)
            return converter(value) * multiplier if value else None
        except ValueError:
            return None
    return RinFile(first_child_text(elem, "FileReference"), first_child_text(elem, "URI"),
                   sha256, algorithm, number("Size", lambda value: int(float(value))),
                   number("BitDepth", int), number("SamplingRate", float, 1000))


def _parse_component(elem):
    return RinRecordingComponent(
        _descendant_text(elem, "RecordingComponentReference", "ComponentId"),
        first_child_text(elem, "Title"), _descendant_text(elem, "Role"),
        first_child_text(elem, "RecordingComponentFileReference"))


def _parse_sound_recording(elem):
    return RinSoundRecording(first_child_text(elem, "ResourceReference"),
                             _descendant_text(elem, "ISRC"),
                             _descendant_text(elem, "TitleText", "Title"),
                             first_child_text(elem, "SoundRecordingFileReference"))


def parse_rin_file(path: str) -> RinParseResult:
    try:
        root = parse_bounded_xml(path)
    except (ET.ParseError, ValueError) as exc:
        return RinParseResult(path, warnings=[f"malformed XML: {exc}"])
    except OSError as exc:
        return RinParseResult(path, warnings=[f"could not read file: {exc}"])
    validation = validate_ddex(root, "rin")
    warnings = [] if validation["status"] == "valid" else [
        f"RIN XSD validation status: {validation['status']}"]
    sessions = [_parse_session(item) for item in iter_by_local_name(root, "Session")]
    components = [_parse_component(item) for item in iter_by_local_name(root, "RecordingComponent")]
    recordings = [_parse_sound_recording(item) for item in iter_by_local_name(root, "SoundRecording")]
    files = [_parse_file(item) for item in iter_by_local_name(root, "File")]
    if not sessions:
        warnings.append("no <Session> elements found")
    if not components:
        warnings.append("no <RecordingComponent> elements found")
    contributors = [_parse_contributor(item) for item in iter_by_local_name(root, "Party")]
    contributors.extend(item for session in sessions for item in session.contributors)
    unique, seen = [], set()
    for item in contributors:
        key = (item.party_id, item.name, item.role)
        if key not in seen:
            seen.add(key)
            unique.append(item)
    equipment = [_parse_equipment(item) for item in iter_by_local_name(root, "Equipment")]
    by_reference = {item.file_reference: item for item in files if item.file_reference}
    bindings = []
    entities = [("recording_component", item.component_id, item.file_reference) for item in components]
    entities += [("sound_recording", item.resource_reference, item.file_reference) for item in recordings]
    for kind, identity, reference in entities:
        if reference:
            info = by_reference.get(reference)
            bindings.append({"entity_type": kind, "entity_reference": identity,
                             "file_reference": reference,
                             "sha256": info.sha256 if info else None,
                             "uri": info.uri if info else None, "resolved": info is not None})
            if info is None:
                warnings.append(f"unresolved RIN file reference: {reference}")
    return RinParseResult(path, namespace_of(root), root.attrib.get("SchemaVersionId"),
                          validation, sessions, unique, equipment, components,
                          recordings, files, bindings, warnings)


def main() -> int:
    parser = argparse.ArgumentParser(description="Parse and validate a DDEX RIN 2.1 XML file.")
    parser.add_argument("path")
    args = parser.parse_args()
    print(json.dumps(dataclasses.asdict(parse_rin_file(args.path)), indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
