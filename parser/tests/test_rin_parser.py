"""
Synthetic parser-behaviour tests for rin_parser.py.

These fixtures are hand-written and are NOT DDEX-compliance tests -- they
only check that the parser extracts the fields it targets, leaves
missing/optional fields as None/empty rather than fabricating values, and
handles malformed XML gracefully. They say nothing about whether the parser
matches a real DDEX RIN document's exact structure (no real sample was
available at implementation time -- see parser/README.md).
"""

import pathlib

from rin_parser import parse_rin_file

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def test_parses_minimal_session_contributor_equipment_component():
    result = parse_rin_file(str(FIXTURES / "rin_minimal.xml"))

    assert len(result.sessions) == 1
    session = result.sessions[0]
    assert session.session_id == "S1"
    assert session.date == "2025-03-01"
    assert session.location == "Studio A"

    assert len(session.contributors) == 1
    assert session.contributors[0].name == "Jane Doe"
    assert session.contributors[0].role == "Producer"

    assert len(session.equipment) == 1
    assert session.equipment[0].description == "Neumann U87"

    assert len(result.recording_components) == 1
    assert result.recording_components[0].title == "Lead Vocal Take 3"

    assert result.contributors == session.contributors
    assert result.equipment == session.equipment
    assert result.warnings == []


def test_missing_optional_fields_stay_none_not_fabricated():
    result = parse_rin_file(str(FIXTURES / "rin_missing_fields.xml"))

    assert len(result.sessions) == 1
    session = result.sessions[0]
    assert session.date is None
    assert session.location is None
    assert session.equipment == []

    assert len(session.contributors) == 1
    assert session.contributors[0].role is None

    assert result.recording_components == []
    assert "no <RecordingComponent> elements found" in result.warnings


def test_malformed_xml_reports_warning_without_crashing():
    result = parse_rin_file(str(FIXTURES / "rin_malformed.xml"))

    assert result.sessions == []
    assert result.warnings
    assert "malformed XML" in result.warnings[0]
