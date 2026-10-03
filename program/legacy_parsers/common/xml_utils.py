"""Bounded, namespace-tolerant XML helpers; not DDEX XSD validation."""

import xml.etree.ElementTree as ET

MAX_XML_BYTES = 4 * 1024 * 1024


def parse_bounded_xml(path):
    with open(path, "rb") as handle:
        data = handle.read(MAX_XML_BYTES + 1)
    if len(data) > MAX_XML_BYTES:
        raise ValueError("XML file exceeds 4 MiB parser limit")
    upper = data.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise ValueError("DTD and entity declarations are not accepted")
    return ET.fromstring(data)


def iter_by_local_name(element, name):
    for child in element.iter():
        if isinstance(child.tag, str) and child.tag.rsplit("}", 1)[-1] == name:
            yield child


def first_child_text(element, name):
    for child in element:
        if isinstance(child.tag, str) and child.tag.rsplit("}", 1)[-1] == name:
            value = "".join(child.itertext()).strip()
            return value or None
    return None
