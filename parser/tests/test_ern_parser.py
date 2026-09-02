"""
Synthetic parser-behaviour tests for ern_parser.py.

Hand-written fixtures, NOT DDEX-compliance tests. Also verifies that
AI-declaration extraction stays unsupported (None/empty) rather than
guessed, per the documented baseline limitation (see ern_parser.py's module
docstring and parser/README.md).
"""

import pathlib

from ern_parser import parse_ern_file

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def test_parses_minimal_release():
    result = parse_ern_file(str(FIXTURES / "ern_minimal.xml"))

    assert len(result.releases) == 1
    release = result.releases[0]
    assert release.release_id == "R1"
    assert release.title == "Test Album"
    assert release.release_type == "Album"


def test_missing_optional_fields_stay_none_not_fabricated():
    result = parse_ern_file(str(FIXTURES / "ern_missing_fields.xml"))

    assert len(result.releases) == 1
    release = result.releases[0]
    assert release.title is None
    assert release.release_type is None


def test_ai_declaration_extraction_is_unsupported_not_guessed():
    for fixture in ("ern_minimal.xml", "ern_missing_fields.xml"):
        result = parse_ern_file(str(FIXTURES / fixture))
        assert result.contains_ai_declared is None
        assert result.ai_contributions == []
        assert any("AI-declaration extraction is unsupported" in w for w in result.warnings)
