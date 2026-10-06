#!/usr/bin/env python3
"""Bounded DDEX ERN 4.3 parser with offline XSD validation and audio binding."""

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
AI_DECLARATION_LIMITATION = (
    "ERN 4.3 release-notification.xsd does not attach the ContainsAI allowed-value "
    "type to an ERN message element; AI disclosure is therefore not inferred from ERN."
)


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
class ErnRelease:
    release_id: Optional[str] = None
    title: Optional[str] = None
    release_type: Optional[str] = None


@dataclass
class ErnParty:
    party_reference: Optional[str] = None
    name: Optional[str] = None


@dataclass
class ErnFile:
    uri: Optional[str] = None
    sha256: Optional[str] = None
    hash_algorithm: Optional[str] = None
    file_size_kib: Optional[float] = None


@dataclass
class ErnResource:
    resource_reference: Optional[str] = None
    isrc: Optional[str] = None
    title: Optional[str] = None
    duration: Optional[str] = None
    contributor_references: List[str] = field(default_factory=list)
    files: List[ErnFile] = field(default_factory=list)


@dataclass
class ErnParseResult:
    source_path: str
    namespace: Optional[str] = None
    schema_version: Optional[str] = None
    xsd_validation: dict[str, Any] = field(default_factory=dict)
    releases: List[ErnRelease] = field(default_factory=list)
    resources: List[ErnResource] = field(default_factory=list)
    parties: List[ErnParty] = field(default_factory=list)
    audio_bindings: List[dict[str, Any]] = field(default_factory=list)
    contains_ai_declared: Optional[bool] = None
    ai_contributions: List[Any] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def _parse_release(elem):
    return ErnRelease(_descendant_text(elem, "ReleaseId", "ReleaseReference"),
                      _descendant_text(elem, "DisplayTitleText", "TitleText", "Title"),
                      _descendant_text(elem, "ReleaseType", "Type"))


def _parse_file(elem):
    hash_sum = _first(elem, "HashSum")
    value = _descendant_text(hash_sum, "HashSumValue") if hash_sum is not None else None
    algorithm = (_descendant_text(hash_sum, "HashSumAlgorithmType") or
                 _descendant_text(hash_sum, "Algorithm")) if hash_sum is not None else None
    sha256 = value.lower() if value and SHA256.fullmatch(value) and (
        not algorithm or "sha" in algorithm.casefold() and "256" in algorithm) else None
    try:
        size = float(first_child_text(elem, "FileSize")) if first_child_text(elem, "FileSize") else None
    except ValueError:
        size = None
    return ErnFile(first_child_text(elem, "URI"), sha256, algorithm, size)


def _parse_resource(elem):
    files = []
    for delivery in iter_by_local_name(elem, "DeliveryFile"):
        file_element = _first(delivery, "File")
        if file_element is not None:
            files.append(_parse_file(file_element))
    contributor_references = []
    for contributor in iter_by_local_name(elem, "Contributor"):
        reference = _descendant_text(contributor, "ContributorPartyReference", "PartyReference")
        if reference:
            contributor_references.append(reference)
    return ErnResource(first_child_text(elem, "ResourceReference"),
                       _descendant_text(elem, "ISRC"),
                       _descendant_text(elem, "DisplayTitleText", "TitleText", "Title"),
                       first_child_text(elem, "Duration"), contributor_references, files)


def parse_ern_file(path: str) -> ErnParseResult:
    try:
        root = parse_bounded_xml(path)
    except (ET.ParseError, ValueError) as exc:
        return ErnParseResult(path, warnings=[f"malformed XML: {exc}", AI_DECLARATION_LIMITATION])
    except OSError as exc:
        return ErnParseResult(path, warnings=[f"could not read file: {exc}", AI_DECLARATION_LIMITATION])
    validation = validate_ddex(root, "ern")
    warnings = [AI_DECLARATION_LIMITATION]
    if validation["status"] != "valid":
        warnings.append(f"ERN XSD validation status: {validation['status']}")
    releases = [_parse_release(item) for item in iter_by_local_name(root, "Release")]
    resources = [_parse_resource(item) for item in iter_by_local_name(root, "SoundRecording")]
    parties = [ErnParty(first_child_text(item, "PartyReference"),
                        _descendant_text(item, "FullName", "PartyName", "Name"))
               for item in iter_by_local_name(root, "Party")]
    if not releases:
        warnings.append("no <Release> elements found")
    bindings = [{"resource_reference": resource.resource_reference,
                 "isrc": resource.isrc, "title": resource.title,
                 "uri": item.uri, "sha256": item.sha256}
                for resource in resources for item in resource.files]
    return ErnParseResult(path, namespace_of(root),
                          root.attrib.get("ReleaseProfileVersionId") or "4.3",
                          validation, releases, resources, parties, bindings,
                          None, [], warnings)


def main() -> int:
    parser = argparse.ArgumentParser(description="Parse and validate a DDEX ERN 4.3 XML file.")
    parser.add_argument("path")
    args = parser.parse_args()
    print(json.dumps(dataclasses.asdict(parse_ern_file(args.path)), indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
