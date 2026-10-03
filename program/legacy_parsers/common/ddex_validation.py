"""Offline, version-pinned DDEX XSD validation helpers."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

try:
    import xmlschema  # type: ignore
except ImportError:
    xmlschema = None

PROGRAM_ROOT = Path(__file__).resolve().parents[2]
REGISTRY = {
    "rin": {"namespace": "http://ddex.net/xml/rin/21", "version": "2.1",
            "schema": PROGRAM_ROOT / "schemas/ddex/rin-2.1/recording-information-notification.xsd"},
    "ern": {"namespace": "http://ddex.net/xml/ern/43", "version": "4.3",
            "schema": PROGRAM_ROOT / "schemas/ddex/ern-4.3/release-notification.xsd"},
}


def namespace_of(root):
    if isinstance(root.tag, str) and root.tag.startswith("{"):
        return root.tag[1:].split("}", 1)[0]
    return None


@lru_cache(maxsize=2)
def _schema(path):
    return xmlschema.XMLSchema(str(path))


def validate_ddex(root, standard):
    config = REGISTRY[standard]
    namespace = namespace_of(root)
    result = {"standard": standard.upper(), "namespace": namespace,
              "supported_version": config["version"],
              "schema_path": str(config["schema"].relative_to(PROGRAM_ROOT)),
              "validator_available": xmlschema is not None,
              "status": "not_assessed", "errors": []}
    if namespace != config["namespace"]:
        result["status"] = "unsupported_version"
        result["errors"] = [f"Expected namespace {config['namespace']!r}; observed {namespace!r}."]
        return result
    if xmlschema is None:
        result["errors"] = ["xmlschema is not installed; XSD validation was not run."]
        return result
    try:
        errors = []
        for error in _schema(config["schema"]).iter_errors(root):
            errors.append(str(error).split("\n", 1)[0])
            if len(errors) == 20:
                errors.append("Additional XSD validation errors were omitted.")
                break
        result["errors"] = errors
        result["status"] = "invalid" if errors else "valid"
    except Exception as exc:
        result["errors"] = [f"XSD validator failed: {exc}"]
    return result
