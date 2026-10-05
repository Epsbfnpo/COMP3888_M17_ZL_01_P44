"""Bounded parsers for machine-readable and human-readable music sheets.

JSON and CSV are the canonical interchange formats. TXT and PDF extraction is
best-effort and is explicitly marked partial when no structured table can be
recovered. Parser observations never authenticate the statements in a sheet.
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
from pathlib import Path

from jsonschema import Draft202012Validator

MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
MAX_PDF_PAGES = 100
MAX_RECORDS = 5000
MAX_EXTRACTED_CHARACTERS = 2_000_000
SHA256 = re.compile(r"^[0-9a-f]{64}$")

BASE = Path(__file__).resolve().parents[1]


class SheetParseError(ValueError):
    pass


def _read_bytes(path):
    path = Path(path)
    with path.open("rb") as handle:
        data = handle.read(MAX_DOCUMENT_BYTES + 1)
    if len(data) > MAX_DOCUMENT_BYTES:
        raise SheetParseError("Sheet exceeds the 8 MiB parser limit.")
    return data


def _json_load(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise SheetParseError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    def invalid_number(value):
        raise SheetParseError(f"Non-finite JSON number is not allowed: {value}")

    try:
        return json.loads(data.decode("utf-8-sig"), object_pairs_hook=pairs,
                          parse_constant=invalid_number)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SheetParseError(f"Invalid UTF-8 JSON sheet: {exc}") from exc


def _validate_json(document, schema_name):
    schema = json.loads((BASE / "schemas" / schema_name).read_text())
    error = next(Draft202012Validator(schema).iter_errors(document), None)
    if error:
        location = "/".join(map(str, error.absolute_path)) or "<root>"
        raise SheetParseError(f"Sheet schema validation failed at {location} ({error.validator}).")


def _seconds(value, field):
    if value in (None, ""):
        return None
    if type(value) in (int, float):
        number = float(value)
    elif isinstance(value, str):
        text = value.strip()
        if ":" in text:
            parts = text.split(":")
            if len(parts) not in (2, 3):
                raise SheetParseError(f"Invalid timecode in {field}.")
            try:
                values = [float(item) for item in parts]
            except ValueError as exc:
                raise SheetParseError(f"Invalid timecode in {field}.") from exc
            number = (values[0] * 60 + values[1] if len(values) == 2
                      else values[0] * 3600 + values[1] * 60 + values[2])
        else:
            try:
                number = float(text)
            except ValueError as exc:
                raise SheetParseError(f"Invalid numeric value in {field}.") from exc
    else:
        raise SheetParseError(f"Invalid value type in {field}.")
    if not math.isfinite(number) or number < 0:
        raise SheetParseError(f"{field} must be a finite non-negative time.")
    return number


def _number(value, field):
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise SheetParseError(f"Invalid numeric value in {field}.") from exc
    if not math.isfinite(number):
        raise SheetParseError(f"{field} must be finite.")
    return number


def _clean_hash(value, field):
    if value in (None, ""):
        return None
    value = str(value).strip().lower()
    if not SHA256.fullmatch(value):
        raise SheetParseError(f"{field} must be a lowercase SHA-256 when supplied.")
    return value


def _clean_text(value):
    if value is None:
        return None
    value = str(value).strip()
    return value or None


ALIASES = {
    "selection": "selection_id", "selectionid": "selection_id",
    "take": "take_id", "takeid": "take_id",
    "sourcehash": "source_hash", "sourcefilehash": "source_hash",
    "targethash": "target_hash", "targetfilehash": "target_hash",
    "sourcestart": "source_start_seconds", "sourcestarttime": "source_start_seconds",
    "sourceend": "source_end_seconds", "sourceendtime": "source_end_seconds",
    "targetstart": "target_start_seconds", "targetstarttime": "target_start_seconds",
    "targetend": "target_end_seconds", "targetendtime": "target_end_seconds",
    "gain": "gain", "lineargain": "gain",
    "cue": "cue_id", "cueid": "cue_id", "cuenumber": "cue_id",
    "cuetitle": "title", "name": "title",
    "start": "start_seconds", "starttime": "start_seconds", "timecode": "start_seconds",
    "duration": "duration_seconds", "end": "end_seconds", "endtime": "end_seconds",
    "assethash": "asset_hash", "audiohash": "asset_hash",
    "usagecode": "usage", "usetype": "usage",
}


def _canonical_header(value):
    normalized = re.sub(r"[^a-z0-9]", "", value.casefold())
    return ALIASES.get(normalized, re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_"))


def _decode_text(data):
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SheetParseError("TXT/CSV sheets must be UTF-8.") from exc


def _extract_pdf(path):
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise SheetParseError("PDF sheet parsing requires pypdf.") from exc
    try:
        reader = PdfReader(str(path), strict=True)
        if reader.is_encrypted:
            raise SheetParseError("Encrypted PDF sheets are not accepted.")
        if len(reader.pages) > MAX_PDF_PAGES:
            raise SheetParseError("PDF sheet exceeds the 100-page parser limit.")
        pages, total = [], 0
        for page in reader.pages:
            text = page.extract_text(extraction_mode="layout") or ""
            total += len(text)
            if total > MAX_EXTRACTED_CHARACTERS:
                raise SheetParseError("Extracted PDF text exceeds the parser limit.")
            pages.append(text)
        return "\n".join(pages)
    except SheetParseError:
        raise
    except Exception as exc:
        raise SheetParseError(f"Could not extract PDF sheet text: {exc}") from exc


def _metadata_and_table(text):
    """Extract simple key:value metadata and an optional delimited table."""
    metadata, table_lines = {}, []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        match = re.match(r"^([A-Za-z][A-Za-z _-]{1,40})\s*:\s*(.+)$", line)
        if match and not table_lines:
            metadata[_canonical_header(match.group(1))] = match.group(2).strip()
        else:
            table_lines.append(raw)
    if not table_lines:
        return metadata, []
    sample = "\n".join(table_lines[:20])
    delimiter = None
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=",\t|").delimiter
    except csv.Error:
        pass
    if delimiter is None:
        return metadata, []
    reader = csv.DictReader(io.StringIO("\n".join(table_lines)), delimiter=delimiter)
    if not reader.fieldnames:
        return metadata, []
    rows = []
    for raw in reader:
        row = {_canonical_header(key): value.strip() if isinstance(value, str) else value
               for key, value in raw.items() if key is not None}
        if any(value not in (None, "") for value in row.values()):
            rows.append(row)
        if len(rows) > MAX_RECORDS:
            raise SheetParseError("Sheet exceeds the 5000-record limit.")
    return metadata, rows


def _load_sheet(path):
    path = Path(path)
    data = _read_bytes(path)
    suffix = path.suffix.casefold()
    if suffix == ".json":
        return "json", _json_load(data), None
    if suffix == ".pdf":
        text = _extract_pdf(path)
        metadata, rows = _metadata_and_table(text)
        return "pdf", {"metadata": metadata, "rows": rows}, text
    if suffix in (".csv", ".txt"):
        text = _decode_text(data)
        metadata, rows = _metadata_and_table(text)
        return suffix[1:], {"metadata": metadata, "rows": rows}, text
    raise SheetParseError("Sheet format must be JSON, CSV, TXT, or PDF.")


def _normalise_comp_record(raw, index):
    record = {
        "selection_id": _clean_text(raw.get("selection_id")) or f"selection-{index}",
        "take_id": _clean_text(raw.get("take_id")),
        "source_hash": _clean_hash(raw.get("source_hash"), "source_hash"),
        "target_hash": _clean_hash(raw.get("target_hash"), "target_hash"),
        "source_start_seconds": _seconds(raw.get("source_start_seconds"), "source_start_seconds"),
        "source_end_seconds": _seconds(raw.get("source_end_seconds"), "source_end_seconds"),
        "target_start_seconds": _seconds(raw.get("target_start_seconds"), "target_start_seconds"),
        "target_end_seconds": _seconds(raw.get("target_end_seconds"), "target_end_seconds"),
        "gain": _number(raw.get("gain"), "gain"),
        "notes": _clean_text(raw.get("notes")),
    }
    if not record["take_id"]:
        raise SheetParseError(f"Comp selection {index} is missing take_id.")
    if record["source_start_seconds"] is None or record["source_end_seconds"] is None:
        raise SheetParseError(f"Comp selection {index} needs source start and end times.")
    if record["source_end_seconds"] <= record["source_start_seconds"]:
        raise SheetParseError(f"Comp selection {index} source end must be after start.")
    if record["target_start_seconds"] is None:
        raise SheetParseError(f"Comp selection {index} needs target_start_seconds.")
    if (record["target_end_seconds"] is not None and
            record["target_end_seconds"] <= record["target_start_seconds"]):
        raise SheetParseError(f"Comp selection {index} target end must be after start.")
    return record


def parse_comp_sheet_file(path):
    input_format, loaded, extracted_text = _load_sheet(path)
    if input_format == "json":
        _validate_json(loaded, "comp-sheet.schema.json")
        metadata = {key: loaded.get(key) for key in ("song_title", "comp_name", "target_hash")
                    if loaded.get(key) is not None}
        raw_records = loaded["selections"]
    else:
        metadata, raw_records = loaded["metadata"], loaded["rows"]
    if len(raw_records) > MAX_RECORDS:
        raise SheetParseError("Comp sheet exceeds the 5000-selection limit.")
    warnings = []
    selections = []
    if raw_records:
        selections = [_normalise_comp_record(item, index)
                      for index, item in enumerate(raw_records, 1)]
        ids = [item["selection_id"] for item in selections]
        if len(ids) != len(set(ids)):
            raise SheetParseError("Comp selection_id values must be unique.")
    else:
        warnings.append("No structured comp selection table was recovered; text extraction only.")
    return {
        "source_path": str(path), "sheet_type": "comp", "input_format": input_format,
        "metadata": metadata, "selections": selections,
        "referenced_source_hashes": sorted({item["source_hash"] for item in selections
                                             if item["source_hash"]}),
        "warnings": warnings,
        "text_excerpt": extracted_text[:2000] if extracted_text and warnings else None,
    }


def _normalise_cue_record(raw, index):
    start = _seconds(raw.get("start_seconds"), "start_seconds")
    duration = _seconds(raw.get("duration_seconds"), "duration_seconds")
    end = _seconds(raw.get("end_seconds"), "end_seconds")
    if start is None:
        raise SheetParseError(f"Cue {index} is missing start_seconds.")
    if duration is None and end is None:
        raise SheetParseError(f"Cue {index} needs duration_seconds or end_seconds.")
    if end is None:
        end = start + duration
    if duration is None:
        duration = end - start
    if duration <= 0 or end <= start or not math.isclose(end - start, duration, abs_tol=0.002):
        raise SheetParseError(f"Cue {index} has inconsistent start/end/duration values.")
    return {
        "cue_id": _clean_text(raw.get("cue_id")) or f"cue-{index}",
        "title": _clean_text(raw.get("title")),
        "composer": _clean_text(raw.get("composer")),
        "publisher": _clean_text(raw.get("publisher")),
        "usage": _clean_text(raw.get("usage")),
        "isrc": _clean_text(raw.get("isrc")),
        "iswc": _clean_text(raw.get("iswc")),
        "asset_hash": _clean_hash(raw.get("asset_hash"), "asset_hash"),
        "start_seconds": start, "duration_seconds": duration, "end_seconds": end,
        "notes": _clean_text(raw.get("notes")),
    }


def parse_cue_sheet_file(path):
    input_format, loaded, extracted_text = _load_sheet(path)
    if input_format == "json":
        _validate_json(loaded, "cue-sheet.schema.json")
        metadata = {key: loaded.get(key) for key in ("production_title", "episode_title", "territory")
                    if loaded.get(key) is not None}
        raw_records = loaded["cues"]
    else:
        metadata, raw_records = loaded["metadata"], loaded["rows"]
    if len(raw_records) > MAX_RECORDS:
        raise SheetParseError("Cue sheet exceeds the 5000-cue limit.")
    warnings = []
    cues = []
    if raw_records:
        cues = [_normalise_cue_record(item, index)
                for index, item in enumerate(raw_records, 1)]
        ids = [item["cue_id"] for item in cues]
        if len(ids) != len(set(ids)):
            raise SheetParseError("Cue IDs must be unique.")
    else:
        warnings.append("No structured cue table was recovered; text extraction only.")
    return {
        "source_path": str(path), "sheet_type": "cue", "input_format": input_format,
        "metadata": metadata, "cues": cues,
        "referenced_asset_hashes": sorted({item["asset_hash"] for item in cues
                                            if item["asset_hash"]}),
        "warnings": warnings,
        "text_excerpt": extracted_text[:2000] if extracted_text and warnings else None,
    }
