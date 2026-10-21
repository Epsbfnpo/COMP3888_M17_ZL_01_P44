"""Header-only WAV probe matching what the evaluator accepts (no Tkinter, no NumPy).

The standard-library ``wave`` module rejects IEEE-float WAV files, so the builder
used to treat valid float32/float64 files as unreadable. This reads only the RIFF
header chunks: integer PCM (format 1), IEEE float (format 3) and WAVE_FORMAT_EXTENSIBLE
whose sub-format is PCM or float. It never decodes samples and never changes a file.
"""

from __future__ import annotations

import struct
from pathlib import Path

PCM, FLOAT, EXTENSIBLE = 1, 3, 0xFFFE


def wav_technical(path) -> dict:
    """Objective fields {duration_seconds, sample_rate_hz, channels, bit_depth}; {} if unsupported."""
    try:
        path = Path(path)
        size = path.stat().st_size
        with path.open("rb") as f:
            head = f.read(12)
            if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
                return {}
            fmt = None
            position = 12
            while position + 8 <= size:
                f.seek(position)
                chunk = f.read(8)
                if len(chunk) < 8:
                    break
                chunk_id, chunk_size = chunk[:4], struct.unpack("<I", chunk[4:])[0]
                body = position + 8
                if chunk_id == b"fmt ":
                    raw = f.read(min(chunk_size, 40))
                    if len(raw) < 16:
                        return {}
                    tag, channels, rate, _byte_rate, block_align, bits = struct.unpack(
                        "<HHIIHH", raw[:16])
                    if tag == EXTENSIBLE:
                        if len(raw) < 26:
                            return {}
                        tag = struct.unpack("<H", raw[24:26])[0]  # sub-format GUID's first field
                    fmt = (tag, channels, rate, block_align, bits)
                elif chunk_id == b"data":
                    if fmt is None:
                        return {}
                    tag, channels, rate, block_align, bits = fmt
                    if (tag not in (PCM, FLOAT) or not rate or not channels or not block_align
                            or (tag == FLOAT and bits not in (32, 64))
                            or (tag == PCM and bits not in (8, 16, 24, 32))):
                        return {}
                    available = max(0, size - body)
                    # Unfinalised files (size 0 or 0xFFFFFFFF) run to the end of the file.
                    data = available if chunk_size in (0, 0xFFFFFFFF) else min(chunk_size, available)
                    frames = data // block_align
                    return {"duration_seconds": round(frames / rate, 6),
                            "sample_rate_hz": rate, "channels": channels, "bit_depth": bits}
                position = body + chunk_size + (chunk_size & 1)
    except (OSError, struct.error, ZeroDivisionError):
        return {}
    return {}
