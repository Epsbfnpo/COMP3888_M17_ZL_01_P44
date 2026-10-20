"""File and evaluator operations, independent of Tkinter."""

from __future__ import annotations

import hashlib
from contextlib import nullcontext
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from threading import Event
from typing import Callable

import manifest_logic as L
import relationship_modes as RM

Progress = Callable[[str], None]


class Cancelled(Exception):
    pass


def check_cancel(cancel: Event) -> None:
    if cancel.is_set():
        raise Cancelled("Operation cancelled.")


def verified_hash(path: Path, cancel: Event, destination: Path | None = None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        with destination.open("wb") if destination else nullcontext() as output:
            while True:
                check_cancel(cancel)
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                if output is not None:
                    output.write(chunk)
    return digest.hexdigest()


def inspect_files(paths: list[str], progress: Progress, cancel: Event) -> list[dict]:
    results = []
    for n, raw in enumerate(paths, 1):
        path = Path(raw)
        progress(f"Reading file {n}/{len(paths)}: {path.name}")
        try:
            size = path.stat().st_size
            if size > RM.MAX_FILE_BYTES:
                raise ValueError(f"File exceeds the {RM.MAX_FILE_BYTES // 2**20} MiB limit.")
            digest = verified_hash(path, cancel)
            technical = L.wav_technical(path) if path.suffix.lower() in (".wav", ".wave") else {}
            results.append({"path": str(path), "hash": digest, "technical": technical})
        except (OSError, ValueError) as exc:
            results.append({"path": str(path), "error": str(exc)})
    return results


def export_bundle(manifest: dict, files: list[dict], target: Path, copy_files: bool,
                  progress: Progress, cancel: Event) -> None:
    """Publish a verified reference manifest or a portable copied bundle."""
    target = Path(target)
    target_root = target.parent.resolve(strict=True)
    published_manifest = json.loads(json.dumps(manifest, ensure_ascii=False))
    published_manifest["objects"] = dict(manifest["objects"])
    created = []
    published = False
    with tempfile.TemporaryDirectory(prefix=".manifest-stage-", dir=target_root) as staging:
        stage = Path(staging)
        try:
            seen = set()
            staged_objects = []
            reserved = set()
            for n, entry in enumerate(files, 1):
                check_cancel(cancel)
                digest = entry["hash"]
                if digest in seen:
                    continue
                seen.add(digest)
                progress(f"Verifying file {n}/{len(files)}: {Path(entry['path']).name}")
                if not entry.get("path"):
                    raise ValueError(
                        f"{entry['id']}: original file is missing. Use Locate/replace to find "
                        "the original file with the declared SHA-256.")
                try:
                    source = Path(entry["path"]).resolve(strict=True)
                except OSError as exc:
                    raise ValueError(
                        f"{entry['id']}: original file is missing or moved. Use Locate/replace "
                        f"to find the original file. ({exc})") from None
                if not source.is_file():
                    raise ValueError(f"{entry['id']}: selected original is not a regular file.")
                actual = verified_hash(source, cancel)
                if actual != digest:
                    raise ValueError(
                        f"{entry['id']} ({source.name}): file content no longer matches its "
                        "declared SHA-256. Use Locate/replace to select the original file; "
                        "the declared identity was not changed.")

                requested = published_manifest["objects"].get(digest)
                if requested is None:
                    raise ValueError(f"{entry['id']}: no object path was declared.")
                if not copy_files:
                    mapped = Path(requested)
                    mapped_source = (mapped if mapped.is_absolute()
                                     else target_root / mapped).resolve()
                    if mapped_source != source:
                        raise ValueError(
                            f"{entry['id']}: the saved reference does not resolve to the "
                            "selected original file.")
                    if not published_manifest.get("options", {}).get(
                            "local_file_references", False):
                        raise ValueError(
                            "Referenced-file submissions must enable local_file_references.")
                    continue

                relative = Path(requested)
                if (relative.is_absolute() or ".." in relative.parts
                        or not relative.parts or relative.parts[0].casefold() != "objects"):
                    raise ValueError("Copied object paths must stay inside the objects folder.")
                candidate = Path(*relative.parts)
                destination = (target_root / candidate).resolve()
                if not destination.is_relative_to(target_root):
                    raise ValueError("Copied object path escapes the submission folder.")
                name_key = str(candidate).casefold()
                if name_key in reserved:
                    candidate = _suffixed_object_path(candidate, digest, target_root, cancel)
                    name_key = str(candidate).casefold()
                reserved.add(name_key)
                destination = (target_root / candidate).resolve()
                if destination.exists() and verified_hash(destination, cancel) != digest:
                    candidate = _suffixed_object_path(candidate, digest, target_root, cancel)
                    destination = (target_root / candidate).resolve()
                published_manifest["objects"][digest] = candidate.as_posix()
                if destination.exists():
                    continue
                staged = stage / candidate
                staged.parent.mkdir(parents=True, exist_ok=True)
                staged_hash = verified_hash(source, cancel, staged)
                if staged_hash != digest:
                    raise ValueError(
                        f"{entry['id']}: source changed while it was being copied.")
                staged_objects.append((candidate, staged, digest, entry["id"]))

            if copy_files and "options" in published_manifest:
                published_manifest["options"]["local_file_references"] = False
            staged_manifest = stage / "submission.json"
            staged_manifest.write_text(
                json.dumps(published_manifest, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
                encoding="utf-8")
            check_cancel(cancel)
            progress("Publishing verified bundle...")
            for relative, staged, digest, entry_id in staged_objects:
                dest = (target_root / relative).resolve()
                dest.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with dest.open("xb") as output, staged.open("rb") as source:
                        created.append(dest)
                        while chunk := source.read(1024 * 1024):
                            check_cancel(cancel)
                            output.write(chunk)
                except FileExistsError:
                    if verified_hash(dest, cancel) != digest:
                        raise ValueError(
                            f"{entry_id}: object destination changed during export; "
                            "no existing file was overwritten.")
            check_cancel(cancel)
            os.replace(staged_manifest, target)
            published = True
        finally:
            if not published:
                for path in created:
                    path.unlink()


def _suffixed_object_path(relative: Path, digest: str, target_root: Path,
                          cancel: Event) -> Path:
    extension = relative.suffix[1:]
    for index in range(1, 1_000_000):
        suffix = f"{extension[:8 - len(str(index))]}{index}"
        candidate = relative.with_suffix(f".{suffix}")
        if candidate == relative:
            continue
        destination = (target_root / candidate).resolve()
        if not destination.exists() or verified_hash(destination, cancel) == digest:
            return candidate
    raise ValueError(f"Could not create a unique object name for {relative.name!r}.")


def assess_manifest(manifest: dict, files: list[dict], progress: Progress,
                    cancel: Event) -> dict:
    # Import the production entry point to use its dependency setup and contract.
    from run import validate

    with tempfile.TemporaryDirectory(prefix="music-assessment-") as staging:
        root = Path(staging)
        staged_manifest = json.loads(json.dumps(manifest))
        staged_manifest["objects"] = L.copied_object_paths(files)
        if "options" in staged_manifest:
            staged_manifest["options"]["local_file_references"] = False
        export_bundle(staged_manifest, files, root / "submission.json", True, progress, cancel)
        check_cancel(cancel)
        progress("Running full assessment...")
        timeout = manifest["options"]["timeout_seconds"]  # v0.6.0: no 60 s cap
        # Same early-stop reserve as run.run_isolated, so slow audio checks return partial results.
        reserve = min(5.0, max(0.5, 0.1 * timeout))
        deadline = time.time() + max(0.0, timeout - reserve)
        payload = json.dumps(
            {"request": staged_manifest, "root": str(root), "mode": "native",
             "deadline": deadline, "allow_local_files": False},
            allow_nan=False)
        command = [sys.executable, "-B", str(Path(__file__).with_name("run.py")), "--worker"]
        process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        started = time.monotonic()
        try:
            first = True
            while True:
                check_cancel(cancel)
                if time.monotonic() - started >= timeout:
                    raise TimeoutError(f"Assessment exceeded the enforced {timeout}-second timeout.")
                try:
                    stdout, stderr = process.communicate(payload if first else None, timeout=0.1)
                    break
                except subprocess.TimeoutExpired:
                    first = False
            if process.returncode:
                raise ValueError(f"Evaluation worker failed: {stderr.strip() or 'no response available'}")
            response = json.loads(stdout)
            validate(response, "scoring-response.schema.json")
            return response
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate()
