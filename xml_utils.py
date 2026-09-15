"""
Shared XML helpers for DDEX RIN/ERN parsing.

DDEX namespace URIs vary by RIN/ERN version, and no XSD is available in this
repo to pin a specific one down, so lookups here match by local (unprefixed)
tag name rather than a hardcoded namespace URI. Deliberately kept to just the
three helpers below -- no speculative abstractions beyond what rin_parser.py
and ern_parser.py both actually need.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Iterator, Optional


def local_name(tag: str) -> str:
    """Strip a Clark-notation namespace, e.g. '{http://ddex.net/xml/rin/20}Session' -> 'Session'."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def iter_by_local_name(root: ET.Element, name: str) -> Iterator[ET.Element]:
    """Yield every element in root's subtree (root included) whose local tag name matches `name`, case-insensitively."""
    target = name.lower()
    for elem in root.iter():
        if local_name(elem.tag).lower() == target:
            yield elem


def first_child_text(elem: ET.Element, name: str) -> Optional[str]:
    """Return the stripped text of the first descendant of `elem` (elem itself excluded) matching `name`, or None if absent/blank."""
    target = name.lower()
    for child in elem.iter():
        if child is elem:
            continue
        if local_name(child.tag).lower() == target:
            return child.text.strip() if child.text and child.text.strip() else None
    return None
