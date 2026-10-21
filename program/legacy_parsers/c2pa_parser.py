#!/usr/bin/env python3
"""
c2pa_parser.py

Baseline reader for C2PA (Coalition for Content Provenance and Authenticity)
manifest data, using the official `c2pa-python` bindings.

Scope (small baseline -- see parser/README.md):
- Detects whether a C2PA manifest is present and reads it via the official
  Reader API. It uses `Reader.try_create()` so a clean manifest absence is
  distinct from a parser failure, and captures `get_validation_state()` and
  `get_validation_results()` separately from the manifest assertions.
- Uses an explicit offline Context (remote manifest and OCSP fetching off).
  Trust can therefore be reported only when the supplied local trust
  configuration and SDK validation state support it.
- Extracts assertions, ingredients, and signature info as a largely raw
  pass-through of whatever the manifest JSON contains for those keys.
  Every field is read with `.get()` and defaults to None/[] -- this is not
  a verified guarantee that the field names match every manifest producer's
  output, just a best-effort baseline extraction.
- Does NOT re-evaluate, score, or trust-judge validation results --
  `validation_status` is passed through as-is, unexamined.
- `c2pa-python` is a required dependency of the evaluator (pinned in
  program/requirements.txt). The import is still guarded so that a broken
  installation degrades to a clear, actionable warning instead of a crash,
  and rin_parser.py/ern_parser.py remain usable standalone.
- No C2PA test fixture is bundled with this baseline -- see parser/README.md
  limitations.

Usage
-----
    python parser/c2pa_parser.py path/to/file.jpg
    (or, from within parser/:  python c2pa_parser.py path/to/file.jpg)
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional

try:
    import c2pa  # type: ignore
except ImportError:
    c2pa = None  # required dependency missing; see module docstring


@dataclass
class C2paAssertion:
    label: Optional[str] = None
    data: Any = None


@dataclass
class C2paIngredient:
    title: Optional[str] = None
    relationship: Optional[str] = None
    format: Optional[str] = None
    instance_id: Optional[str] = None
    document_id: Optional[str] = None
    referenced_hashes: List[str] = field(default_factory=list)


@dataclass
class C2paSignatureInfo:
    issuer: Optional[str] = None
    time: Optional[str] = None
    alg: Optional[str] = None
    common_name: Optional[str] = None
    cert_serial_number: Optional[str] = None


@dataclass
class C2paParseResult:
    source_path: str
    sdk_available: bool = False
    reader_succeeded: bool = False
    present: bool = False
    active_manifest_label: Optional[str] = None
    assertions: List[C2paAssertion] = field(default_factory=list)
    ingredients: List[C2paIngredient] = field(default_factory=list)
    digital_source_types: List[str] = field(default_factory=list)
    signature_info: Optional[C2paSignatureInfo] = None
    validation_status: Any = None
    validation_state: Optional[str] = None
    validation_results: Any = None
    embedded: Optional[bool] = None
    remote_url: Optional[str] = None
    # Set when the signing certificate is on the local known-AI-generation-signer
    # list. This identifies the signer only; it is not a trust decision.
    known_ai_signer: Optional[dict] = None
    # trusted_c2pa, trusted_legacy_itl, untrusted or invalid, from the pinned
    # trust lists; None when no manifest was validated.
    trust_status: Optional[str] = None
    trust_source: Optional[dict] = None
    trust_note: Optional[str] = None
    revocation_status: Optional[str] = None
    warnings: List[str] = field(default_factory=list)


def _parse_assertion(raw: dict) -> C2paAssertion:
    return C2paAssertion(label=raw.get("label"), data=raw.get("data"))


SHA256 = re.compile(r"(?:sha256:)?([0-9a-fA-F]{64})$")


def _walk(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key, item
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def _hashes(value):
    found = set()
    for _, item in _walk(value):
        if isinstance(item, str):
            match = SHA256.fullmatch(item.strip())
            if match:
                found.add(match.group(1).lower())
    return sorted(found)


def _digital_source_types(value):
    found = set()
    for key, item in _walk(value):
        if str(key).casefold().replace("_", "") == "digitalsourcetype":
            if isinstance(item, str):
                found.add(item)
            elif isinstance(item, list):
                found.update(entry for entry in item if isinstance(entry, str))
    return sorted(found)


def _parse_ingredient(raw: dict) -> C2paIngredient:
    return C2paIngredient(
        title=raw.get("title"),
        relationship=raw.get("relationship"),
        format=raw.get("format"),
        instance_id=raw.get("instance_id") or raw.get("instanceId"),
        document_id=raw.get("document_id") or raw.get("documentId"),
        referenced_hashes=_hashes(raw),
    )


def _parse_signature_info(raw: Optional[dict]) -> Optional[C2paSignatureInfo]:
    if not raw:
        return None
    return C2paSignatureInfo(issuer=raw.get("issuer"), time=raw.get("time"), alg=raw.get("alg"),
                             common_name=raw.get("common_name"),
                             cert_serial_number=raw.get("cert_serial_number"))


TRUST_DIRECTORY = Path(__file__).resolve().parents[1] / "policies" / "c2pa_trust"
TRUST_LISTS_PATH = TRUST_DIRECTORY / "trust_lists.json"
KNOWN_AI_SIGNERS_PATH = TRUST_DIRECTORY / "known_ai_signers.json"
_TRUST_CACHE: dict = {}


class TrustListIntegrityError(RuntimeError):
    """A pinned trust file is missing or differs from its recorded SHA-256."""


def load_trust_configuration(path=None) -> dict:
    """Load the pinned trust lists, refusing any file whose SHA-256 differs from the pin."""
    path = Path(path or TRUST_LISTS_PATH)
    key = str(path.resolve())
    if key in _TRUST_CACHE:
        return _TRUST_CACHE[key]
    try:
        raw = path.read_bytes()
        manifest = json.loads(raw)
        lists = {}
        for list_id in ("official", "legacy_itl"):
            entry = manifest[list_id]
            contents, digests = {}, {}
            for role, item in entry["files"].items():
                data = (path.parent / item["path"]).read_bytes()
                if hashlib.sha256(data).hexdigest() != item["sha256"]:
                    raise TrustListIntegrityError(
                        f"C2PA trust file {item['path']} does not match its pinned SHA-256; "
                        "refusing to assess attestation with an unverified trust list.")
                contents[role] = data.decode("utf-8")
                digests[item["path"]] = item["sha256"]
            lists[list_id] = {"label": entry["label"], "source": entry["source"], "commit": entry["commit"],
                              "frozen_on": entry.get("frozen_on"), "contents": contents, "file_sha256": digests}
    except TrustListIntegrityError:
        raise
    except (OSError, ValueError, KeyError) as exc:
        raise TrustListIntegrityError(f"C2PA trust configuration {path} could not be loaded: {exc}") from exc
    config = {"manifest_sha256": hashlib.sha256(raw).hexdigest(), "lists": lists}
    _TRUST_CACHE[key] = config
    return config


def trust_configuration_record(path=None) -> dict:
    """The pinned trust list versions, for recording in every result (no file contents)."""
    config = load_trust_configuration(path)
    return {"trust_lists_manifest_sha256": config["manifest_sha256"],
            "revocation_status": "not_checked",
            "lists": {list_id: {key: value for key, value in item.items() if key != "contents"}
                      for list_id, item in config["lists"].items()}}


def _trust_settings(trust_list=None) -> dict:
    settings = {"verify": {"remote_manifest_fetch": False, "ocsp_fetch": False,
                           "verify_trust": trust_list is not None}}
    if trust_list is not None:
        # No SDK setting exists for TSA anchors; an empty value is rejected by the SDK.
        settings["trust"] = {role: text for role, text in trust_list["contents"].items()
                             if role != "tsa_trust_anchors" and text.strip()}
    return settings


def _source(trust_list) -> dict:
    return {"label": trust_list["label"], "source": trust_list["source"], "commit": trust_list["commit"]}


def _signed_before(signature: Optional[dict], frozen_on: Optional[str]) -> bool:
    """True only when the SDK reports a signing time earlier than the ITL freeze date."""
    signed = (signature or {}).get("time")
    if not signed or not frozen_on:
        return False
    try:
        when = datetime.datetime.fromisoformat(str(signed).replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=datetime.timezone.utc)
        return when < datetime.datetime.fromisoformat(frozen_on).replace(tzinfo=datetime.timezone.utc)
    except ValueError:
        return False


def _trust_status(path: str, state: Optional[str], signature: Optional[dict], trust: dict):
    """Classify the signer against the official list first, then the frozen legacy ITL."""
    official, legacy = trust["lists"]["official"], trust["lists"]["legacy_itl"]
    normalized = str(state or "").casefold()
    if normalized == "invalid":
        return "invalid", None, None
    if normalized == "trusted":
        return "trusted_c2pa", _source(official), None
    context = None
    try:
        context = c2pa.Context.from_dict(_trust_settings(legacy))
        reader = c2pa.Reader.try_create(path, context=context)
        if reader is None:
            return "untrusted", None, None
        with reader:
            legacy_state = str(reader.get_validation_state() or "").casefold()
    except Exception:
        return "untrusted", None, None
    finally:
        if context is not None:
            context.close()
    if legacy_state != "trusted":
        return "untrusted", None, None
    if _signed_before(signature, legacy["frozen_on"]):
        return "trusted_legacy_itl", _source(legacy), None
    return "untrusted", None, (
        "The signer chains to the legacy Interim Trust List, but no signing time before its "
        f"{legacy['frozen_on']} freeze was reported, so it is not treated as trusted.")


_FINGERPRINT = re.compile(r"^[A-Za-z0-9+/]{43}=$")


def load_known_ai_signers(path=None) -> List[dict]:
    """Signing certificates of known AI generation tools, matched by SHA-256 fingerprint.

    The SDK only accepts base64-encoded SHA-256 fingerprints in an allowed list;
    a hex value would be ignored silently, so the format is checked here.
    """
    path = Path(path or KNOWN_AI_SIGNERS_PATH)
    if not path.exists():
        return []
    signers = json.loads(path.read_text(encoding="utf-8")).get("signers") or []
    for signer in signers:
        if not str(signer.get("name") or "").strip():
            raise ValueError(f"{path}: every known AI signer needs a name.")
        if not _FINGERPRINT.match(str(signer.get("fingerprint_sha256_base64") or "")):
            raise ValueError(f"{path}: {signer['name']} needs a base64 SHA-256 certificate fingerprint.")
    return signers


def _codes(value) -> List[str]:
    if isinstance(value, dict):
        return [item for key, child in value.items()
                for item in ([child] if key == "code" and isinstance(child, str) else _codes(child))]
    if isinstance(value, list):
        return [item for child in value for item in _codes(child)]
    return []


def _known_ai_signer(path: str, signature: Optional[dict]) -> Optional[dict]:
    """Match the signing certificate against the known-AI-signer list.

    The SDK compares certificate fingerprints when they are supplied as an
    allowed list, so the file is read once more with only that list. A match is
    reported even when the manifest itself is invalid (for example the audio was
    edited after signing): it still identifies who signed the AI label.
    """
    signers = load_known_ai_signers()
    if not signers:
        return None
    allowed = "\n".join(signer["fingerprint_sha256_base64"] for signer in signers) + "\n"
    context = c2pa.Context.from_dict({
        "verify": {"remote_manifest_fetch": False, "ocsp_fetch": False, "verify_trust": True},
        "trust": {"allowed_list": allowed},
    })
    try:
        reader = c2pa.Reader.try_create(path, context=context)
        if reader is None:
            return None
        with reader:
            codes = _codes(reader.get_validation_results())
    except Exception:
        return None
    finally:
        context.close()
    if "signingCredential.trusted" not in codes:
        return None
    serial = (signature or {}).get("cert_serial_number")
    matches = [signer for signer in signers
               if serial and str(signer.get("certificate_serial_number")) == str(serial)]
    entry = matches[0] if len(matches) == 1 else None
    return {"name": entry["name"] if entry else "Unidentified entry on the known AI signer list",
            "fingerprint_sha256_base64": entry["fingerprint_sha256_base64"] if entry else None,
            "match_method": "sdk_allowed_list_certificate_fingerprint"}


def parse_c2pa_file(path: str) -> C2paParseResult:
    """Read whatever C2PA manifest data is present in `path`, if any.

    A file with no embedded manifest is the common case, not an error -- it
    is reported as present=False with a warning, never raised to the caller.
    """
    if c2pa is None:
        return C2paParseResult(
            source_path=path,
            sdk_available=False,
            warnings=[
                "c2pa-python is not installed; it is a required dependency. "
                "Install it with pip install -r requirements.txt to enable C2PA parsing."
            ],
        )

    # Raises TrustListIntegrityError (not caught here) when a pinned file was changed.
    trust = load_trust_configuration()
    context = None
    try:
        context = c2pa.Context.from_dict(_trust_settings(trust["lists"]["official"]))
        reader = c2pa.Reader.try_create(path, context=context)
        if reader is None:
            return C2paParseResult(source_path=path, sdk_available=True,
                                   reader_succeeded=True, present=False,
                                   warnings=["C2PA SDK read the asset and found no manifest"])
        with reader:
            store = json.loads(reader.json())
            validation_state = reader.get_validation_state()
            validation_results = reader.get_validation_results()
            embedded = reader.is_embedded()
            remote_url = reader.get_remote_url()
    except Exception as exc:
        # Mirrors the c2pa-python project's own example, which wraps Reader
        # construction/reading in a broad except -- "no manifest present"
        # and genuine read errors aren't reliably distinguishable via a
        # documented exception type at this baseline, so both are reported
        # here as present=False with the underlying message preserved.
        return C2paParseResult(source_path=path, sdk_available=True, reader_succeeded=False,
                               present=False, warnings=[f"no C2PA manifest read: {exc}"])
    finally:
        if context is not None:
            context.close()

    active_label = store.get("active_manifest")
    manifests = store.get("manifests") or {}
    manifest = manifests.get(active_label) if active_label else None

    if manifest is None:
        return C2paParseResult(
            source_path=path,
            sdk_available=True,
            reader_succeeded=True,
            present=False,
            validation_status=store.get("validation_status"),
            validation_state=validation_state,
            validation_results=validation_results,
            embedded=embedded,
            remote_url=remote_url,
            warnings=["manifest store read but no active manifest found"],
        )

    trust_status, trust_source, trust_note = _trust_status(
        path, validation_state, manifest.get("signature_info"), trust)
    return C2paParseResult(
        source_path=path,
        sdk_available=True,
        reader_succeeded=True,
        present=True,
        active_manifest_label=active_label,
        assertions=[_parse_assertion(a) for a in manifest.get("assertions") or []],
        ingredients=[_parse_ingredient(i) for i in manifest.get("ingredients") or []],
        digital_source_types=_digital_source_types(manifest),
        signature_info=_parse_signature_info(manifest.get("signature_info")),
        validation_status=store.get("validation_status"),
        validation_state=validation_state,
        validation_results=validation_results,
        embedded=embedded,
        remote_url=remote_url,
        known_ai_signer=_known_ai_signer(path, manifest.get("signature_info")),
        trust_status=trust_status,
        trust_source=trust_source,
        trust_note=trust_note,
        revocation_status="not_checked",
    )


def main() -> int:
    arg_parser = argparse.ArgumentParser(
        description="Read baseline C2PA manifest data (assertions/ingredients/signature info) from a file."
    )
    arg_parser.add_argument("path", help="Path to a media file that may contain an embedded C2PA manifest")
    args = arg_parser.parse_args()

    result = parse_c2pa_file(args.path)
    print(json.dumps(dataclasses.asdict(result), indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
