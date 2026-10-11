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
            if size > L.MAX_FILE_BYTES:
                raise ValueError(f"File exceeds the {L.MAX_FILE_BYTES // 2**20} MiB limit.")
            digest = verified_hash(path, cancel)
            technical = L.wav_technical(path) if path.suffix.lower() in (".wav", ".wave") else {}
            results.append({"path": str(path), "hash": digest, "technical": technical})
        except (OSError, ValueError) as exc:
            results.append({"path": str(path), "error": str(exc)})
    return results


def export_bundle(manifest: dict, files: list[dict], target: Path, copy_files: bool,
                  progress: Progress, cancel: Event) -> None:
    """Publish the manifest last; never overwrite existing object content."""
    target = Path(target)
    created = []
    published = False
    with tempfile.TemporaryDirectory(prefix=".manifest-stage-", dir=target.parent) as staging:
        stage = Path(staging)
        try:
            for n, entry in enumerate(files, 1):
                check_cancel(cancel)
                relative = manifest["objects"][entry["hash"]]
                dest = target.parent / relative
                staged = stage / relative
                progress(f"Verifying file {n}/{len(files)}: {entry['id']}")
                if copy_files:
                    if not entry["path"]:
                        raise ValueError(f"{entry['id']}: source file is missing; remove and add it again.")
                    staged.parent.mkdir(parents=True, exist_ok=True)
                    digest = verified_hash(Path(entry["path"]), cancel, staged)
                    if digest != entry["hash"]:
                        raise ValueError(f"{entry['id']}: file changed since import. Remove and add it again.")
                if dest.exists() or not copy_files:
                    if verified_hash(dest, cancel) != entry["hash"]:
                        raise ValueError(f"{entry['id']}: existing bundle object has an incorrect hash.")
            staged_manifest = stage / "submission.json"
            staged_manifest.write_text(
                json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
                encoding="utf-8")
            check_cancel(cancel)
            progress("Publishing verified bundle...")
            if copy_files:
                for entry in files:
                    relative = manifest["objects"][entry["hash"]]
                    dest = target.parent / relative
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    # Exclusive creation avoids replacing an existing bundle object.
                    try:
                        with dest.open("xb") as output, (stage / relative).open("rb") as source:
                            created.append(dest)
                            while chunk := source.read(1024 * 1024):
                                check_cancel(cancel)
                                output.write(chunk)
                    except FileExistsError:
                        if verified_hash(dest, cancel) != entry["hash"]:
                            raise ValueError(f"{entry['id']}: bundle object changed during export.")
            check_cancel(cancel)
            os.replace(staged_manifest, target)
            published = True
        finally:
            if not published:
                for path in created:
                    path.unlink()


def assess_manifest(manifest: dict, files: list[dict], progress: Progress,
                    cancel: Event) -> dict:
    # Import the production entry point to use its dependency setup and contract.
    from run import validate

    with tempfile.TemporaryDirectory(prefix="music-assessment-") as staging:
        root = Path(staging)
        export_bundle(manifest, files, root / "submission.json", True, progress, cancel)
        check_cancel(cancel)
        progress("Running full assessment...")
        timeout = min(manifest["options"]["timeout_seconds"], 60)
        payload = json.dumps({"request": manifest, "root": str(root), "mode": "native"},
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
