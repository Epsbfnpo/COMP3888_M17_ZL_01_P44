"""
Minimal tests for c2pa_parser.py.

No external C2PA test asset is bundled with this baseline -- sourcing a
real signed test image was deliberately kept out of scope for this pass
(see parser/README.md limitations). These tests only cover behaviour that
doesn't require a real fixture: a missing/unreadable file, and the
"c2pa-python not installed" code path.
"""

import importlib.util

import pytest

from c2pa_parser import parse_c2pa_file

C2PA_INSTALLED = importlib.util.find_spec("c2pa") is not None


def test_missing_file_reports_not_present_without_crashing(tmp_path):
    missing = tmp_path / "does_not_exist.jpg"

    result = parse_c2pa_file(str(missing))

    assert result.present is False
    assert result.warnings


@pytest.mark.skipif(C2PA_INSTALLED, reason="only exercises the c2pa-python-not-installed code path")
def test_reports_actionable_warning_when_c2pa_python_not_installed(tmp_path):
    dummy = tmp_path / "dummy.bin"
    dummy.write_bytes(b"not a real media file")

    result = parse_c2pa_file(str(dummy))

    assert result.present is False
    assert "c2pa-python is not installed" in result.warnings[0]
