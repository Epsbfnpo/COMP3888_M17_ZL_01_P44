"""Conservative PCM WAV content-derivation assessments.

Each pass verifies only a narrow, declared transformation model. A poor fit
contradicts that model; it does not prove that a source was never used through
some undocumented or nonlinear production process.
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

MAX_SOURCES = 16
MAX_COMP_SELECTIONS = 64
MAX_ANALYSIS_SECONDS = 60
ANALYSIS_RATE_HZ = 2000
MAX_OFFSET_SECONDS = 5.0
COMPLEX_PROCESSING_WORDS = {
    "compress", "limiter", "master", "time stretch", "pitch shift",
    "distortion", "saturat", "reverb", "nonlinear",
}


def _pcm_value(raw: bytes, width: int) -> float:
    if width == 1:
        return (raw[0] - 128) / 128.0
    if width == 2:
        return struct.unpack("<h", raw)[0] / 32768.0
    if width == 3:
        value = int.from_bytes(raw, "little", signed=False)
        if value & 0x800000:
            value -= 1 << 24
        return value / 8388608.0
    if width == 4:
        return struct.unpack("<i", raw)[0] / 2147483648.0
    raise ValueError("Only 8/16/24/32-bit integer PCM WAV is supported.")


def _read_pcm(path: Path):
    try:
        with wave.open(str(path), "rb") as handle:
            if handle.getcomptype() != "NONE":
                raise ValueError("compressed WAV is not supported")
            channels = handle.getnchannels()
            width = handle.getsampwidth()
            rate = handle.getframerate()
            frame_count = min(handle.getnframes(), rate * MAX_ANALYSIS_SECONDS)
            raw = handle.readframes(frame_count)
    except wave.Error as exc:
        raise ValueError(f"invalid WAV: {exc}") from exc
    if channels < 1 or rate < 1 or width not in (1, 2, 3, 4):
        raise ValueError("unsupported PCM channel, rate, or sample width")
    stride = channels * width
    if len(raw) % stride:
        raise ValueError("truncated PCM frame data")
    frames = []
    for position in range(0, len(raw), stride):
        frames.append(tuple(_pcm_value(raw[position + channel * width:
                                           position + (channel + 1) * width], width)
                            for channel in range(channels)))
    return {"frames": frames, "sample_rate_hz": rate, "channels": channels,
            "bit_depth": width * 8}


def _solve(matrix, vector):
    size = len(vector)
    augmented = [list(matrix[row]) + [vector[row]] for row in range(size)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-12:
            return None
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        augmented[column] = [value / divisor for value in augmented[column]]
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            augmented[row] = [current - factor * base
                              for current, base in zip(augmented[row], augmented[column])]
    return [augmented[row][-1] for row in range(size)]


def _metrics(target, sources):
    count = len(target)
    gram = [[0.0 for _ in sources] for _ in sources]
    projection = [0.0 for _ in sources]
    for index in range(count):
        values = [source[index] for source in sources]
        for left, value in enumerate(values):
            projection[left] += value * target[index]
            for right in range(left, len(values)):
                gram[left][right] += value * values[right]
                gram[right][left] = gram[left][right]
    ridge = max(sum(gram[index][index] for index in range(len(sources))) * 1e-10, 1e-12)
    for index in range(len(sources)):
        gram[index][index] += ridge
    gains = _solve(gram, projection)
    if gains is None:
        return None
    predicted = [sum(gain * source[index] for gain, source in zip(gains, sources))
                 for index in range(count)]
    target_energy = sum(value * value for value in target)
    if target_energy < 1e-12:
        return None
    residual_energy = sum((actual - estimate) ** 2
                          for actual, estimate in zip(target, predicted))
    target_mean = sum(target) / count
    predicted_mean = sum(predicted) / count
    covariance = sum((actual - target_mean) * (estimate - predicted_mean)
                     for actual, estimate in zip(target, predicted))
    target_variance = sum((actual - target_mean) ** 2 for actual in target)
    predicted_variance = sum((estimate - predicted_mean) ** 2 for estimate in predicted)
    denominator = math.sqrt(target_variance * predicted_variance)
    correlation = covariance / denominator if denominator > 1e-12 else 0.0
    return gains, math.sqrt(residual_energy / target_energy), correlation


def _base(rule, pass_name, target, edges):
    return {
        "rule": rule,
        "pass": pass_name,
        "target_hash": target["artefact_hash"],
        "source_hashes": sorted({edge["hash"] for edge in edges}),
    }


def _processing_is_complex(edges):
    descriptions = " ".join(str((edge.get("attributes") or {}).get(
        "transformation_description") or "") for edge in edges).casefold()
    return any(word in descriptions for word in COMPLEX_PROCESSING_WORDS)


def _read_target_and_sources(target, edges, chain):
    target_audio = _read_pcm(Path(chain.artefacts[target["artefact_hash"]].get_file()))
    sources = [_read_pcm(Path(chain.artefacts[edge["hash"]].get_file()))
               for edge in edges]
    if any(item["sample_rate_hz"] != target_audio["sample_rate_hz"] or
           item["channels"] != target_audio["channels"] for item in sources):
        raise ValueError("source and target sample rates/channels must match")
    return target_audio, sources


def _linear_timeline_metrics(target_audio, edge_audio, group_keys=None):
    """Fit constant gains after placing declared source ranges on a target timeline."""
    rate = target_audio["sample_rate_hz"]
    step = max(1, rate // ANALYSIS_RATE_HZ)
    target_indices = list(range(0, len(target_audio["frames"]), step))
    target_samples = [sample for index in target_indices
                      for sample in target_audio["frames"][index]]
    grouped = {}
    offsets = []
    for position, (edge, audio) in enumerate(edge_audio):
        attrs = edge.get("attributes") or {}
        source_start = float(attrs.get("source_start_seconds") or 0)
        target_start = float(attrs.get("target_start_seconds") or 0)
        source_end = attrs.get("source_end_seconds")
        target_end = attrs.get("target_end_seconds")
        if source_end is None and target_end is None:
            duration = len(audio["frames"]) / rate - source_start
            source_end = source_start + duration
            target_end = target_start + duration
        elif source_end is None:
            source_end = source_start + float(target_end) - target_start
        elif target_end is None:
            target_end = target_start + float(source_end) - source_start
        source_end, target_end = float(source_end), float(target_end)
        if min(source_start, target_start) < 0 or source_end <= source_start or target_end <= target_start:
            raise ValueError("declared source/target ranges must be positive and ordered")
        if abs((source_end - source_start) - (target_end - target_start)) > max(0.002, 2 / rate):
            raise ValueError("declared ranges imply time stretching")
        if source_end > len(audio["frames"]) / rate + 2 / rate:
            raise ValueError("declared source range exceeds the analysed audio")
        if target_end > len(target_audio["frames"]) / rate + 2 / rate:
            raise ValueError("declared target range exceeds the analysed audio")
        if abs(target_start - source_start) > MAX_OFFSET_SECONDS and not (
                attrs.get("source_end_seconds") is not None and
                attrs.get("target_end_seconds") is not None):
            raise ValueError(f"a declared offset exceeds {MAX_OFFSET_SECONDS:g} seconds")
        fade_in = float(attrs.get("fade_in_seconds") or 0)
        fade_out = float(attrs.get("fade_out_seconds") or 0)
        target_duration = target_end - target_start
        if min(fade_in, fade_out) < 0 or fade_in + fade_out > target_duration + 2 / rate:
            raise ValueError("declared fades must fit within the target range")
        offsets.append(target_start - source_start)
        aligned = []
        silence = (0.0,) * target_audio["channels"]
        for target_index in target_indices:
            target_time = target_index / rate
            frame = silence
            if target_start <= target_time < target_end:
                source_time = source_start + target_time - target_start
                source_index = round(source_time * rate)
                if 0 <= source_index < len(audio["frames"]):
                    envelope = 1.0
                    relative_time = target_time - target_start
                    if fade_in:
                        envelope = min(envelope, relative_time / fade_in)
                    if fade_out:
                        envelope = min(envelope, (target_end - target_time) / fade_out)
                    frame = tuple(value * max(0.0, min(1.0, envelope))
                                  for value in audio["frames"][source_index])
            aligned.extend(frame)
        key = group_keys[position] if group_keys else position
        if key in grouped:
            grouped[key] = [left + right for left, right in zip(grouped[key], aligned)]
        else:
            grouped[key] = aligned
    if len(target_indices) < 32:
        return None
    metrics = _metrics(target_samples, list(grouped.values()))
    return (metrics, offsets, round(rate / step, 3)) if metrics else None


def _linear_status(metrics, match_rmse, match_correlation,
                   contradiction_rmse=0.35, contradiction_correlation=0.5):
    gains, normalized_rmse, correlation = metrics
    if normalized_rmse <= match_rmse and correlation >= match_correlation:
        return "matched"
    if normalized_rmse >= contradiction_rmse or correlation < contradiction_correlation:
        return "contradicted"
    return "not_applicable"


def _linear_pass(native, chain, *, relationship, target_type, scopes, rule,
                 pass_name, match_rmse, match_correlation):
    results = []
    for target in native["artefacts"]:
        edges = [edge for edge in target.get("evidence", [])
                 if edge["relationship_type"] == relationship]
        if not edges:
            continue
        base = _base(rule, pass_name, target, edges)
        if target["artefact_type"] != target_type:
            results.append({**base, "status": "not_applicable",
                            "reason": f"Target is not declared as {target_type}."})
            continue
        declared_scopes = {(edge.get("attributes") or {}).get("derivation_scope")
                           for edge in edges}
        if not declared_scopes or not declared_scopes.issubset(scopes):
            expected = " or ".join(sorted(scopes))
            results.append({**base, "status": "not_applicable",
                            "reason": f"Every {relationship} edge must explicitly declare derivation_scope={expected}."})
            continue
        if _processing_is_complex(edges):
            results.append({**base, "status": "not_applicable",
                            "reason": "The relationship declares processing outside the constant-gain PCM model."})
            continue
        if len(edges) > MAX_SOURCES:
            results.append({**base, "status": "not_applicable",
                            "reason": f"More than {MAX_SOURCES} sources exceeds the bounded analysis limit."})
            continue
        try:
            target_audio, sources = _read_target_and_sources(target, edges, chain)
            assessed = _linear_timeline_metrics(target_audio, list(zip(edges, sources)),
                                                [edge["hash"] for edge in edges])
        except (OSError, ValueError, TypeError) as exc:
            results.append({**base, "status": "not_applicable",
                            "reason": f"PCM analysis unavailable: {exc}"})
            continue
        if assessed is None:
            results.append({**base, "status": "not_applicable",
                            "reason": "There is insufficient non-silent audio for a stable comparison."})
            continue
        metrics, offsets, analysis_rate = assessed
        status = _linear_status(metrics, match_rmse, match_correlation)
        if status == "matched":
            reason = "The target is reconstructed by the declared aligned linear sources within tolerance."
        elif status == "contradicted":
            reason = "The target is not consistent with the explicitly declared aligned constant-gain model."
        else:
            reason = "The fit is inconclusive; nonlinear or undocumented processing may be present."
        gains, normalized_rmse, correlation = metrics
        results.append({**base, "status": status, "reason": reason,
                        "verified_scope": next(iter(declared_scopes)),
                        "gains": [round(value, 6) for value in gains],
                        "offsets_seconds": offsets,
                        "normalized_rmse": round(normalized_rmse, 6),
                        "correlation": round(correlation, 6),
                        "analysis_sample_rate_hz": analysis_rate})
    return results


class AudioDerivationPass:
    """Assess explicitly declared, aligned linear PCM mixes."""

    @staticmethod
    def evaluate(native, chain):
        return _linear_pass(
            native, chain, relationship="mixed_from", target_type="audio/mix",
            scopes={"linear_mix"}, rule="audio_linear_mix_derivation",
            pass_name="AudioDerivationPass", match_rmse=0.02,
            match_correlation=0.995)


class EditDerivationPass:
    """Verify declared constant-gain crops, placements and simple splices."""

    @staticmethod
    def evaluate(native, chain):
        return _linear_pass(
            native, chain, relationship="edited_from", target_type="audio/edited-take",
            scopes={"linear_edit"}, rule="audio_linear_edit_derivation",
            pass_name="EditDerivationPass", match_rmse=0.03,
            match_correlation=0.99)


class StemDerivationPass:
    """Verify stems declared as constant-gain sums of aligned source audio."""

    @staticmethod
    def evaluate(native, chain):
        return _linear_pass(
            native, chain, relationship="stemmed_from", target_type="audio/stem",
            scopes={"linear_stem", "linear_mix"}, rule="audio_linear_stem_derivation",
            pass_name="StemDerivationPass", match_rmse=0.025,
            match_correlation=0.993)


class CompDerivationPass:
    """Verify CompSheet selections against the audio/edited-take they describe."""

    @staticmethod
    def evaluate(native, chain, parser_observations):
        nodes = {node["artefact_hash"]: node for node in native["artefacts"]}
        sheets = [parsed["data"] for parsed in parser_observations.values()
                  if parsed.get("parser") == "comp-sheet" and parsed.get("data")]
        results = []
        for target in native["artefacts"]:
            graph_edges = [edge for edge in target.get("evidence", [])
                           if edge["relationship_type"] == "comped_from"]
            if not graph_edges:
                continue
            base = _base("audio_comp_sheet_derivation", "CompDerivationPass",
                         target, graph_edges)
            selections = []
            for sheet in sheets:
                default_target = (sheet.get("metadata") or {}).get("target_hash")
                selections.extend(selection for selection in sheet.get("selections", [])
                                  if (selection.get("target_hash") or default_target) ==
                                  target["artefact_hash"])
            if not selections:
                results.append({**base, "status": "not_applicable",
                                "reason": "No parsed CompSheet selections identify this edited take."})
                continue
            if len(selections) > MAX_COMP_SELECTIONS:
                results.append({**base, "status": "not_applicable",
                                "reason": f"More than {MAX_COMP_SELECTIONS} selections exceeds the bounded analysis limit."})
                continue
            allowed_sources = {edge["hash"] for edge in graph_edges}
            selected_sources = {selection.get("source_hash") for selection in selections}
            if None in selected_sources:
                results.append({**base, "status": "not_applicable",
                                "reason": "Every assessed CompSheet selection needs a source_hash."})
                continue
            if not selected_sources.issubset(allowed_sources):
                results.append({**base, "status": "contradicted",
                                "reason": "A CompSheet source is not connected by a comped_from relationship.",
                                "selection_count": len(selections)})
                continue
            try:
                target_audio = _read_pcm(Path(chain.artefacts[target["artefact_hash"]].get_file()))
                entries = []
                for selection in selections:
                    source_hash = selection["source_hash"]
                    if source_hash not in nodes:
                        raise ValueError("a CompSheet source hash is absent from the graph")
                    source_audio = _read_pcm(Path(chain.artefacts[source_hash].get_file()))
                    if (source_audio["sample_rate_hz"] != target_audio["sample_rate_hz"] or
                            source_audio["channels"] != target_audio["channels"]):
                        raise ValueError("source and target sample rates/channels must match")
                    entries.append(({
                        "hash": source_hash,
                        "attributes": {
                            "source_start_seconds": selection["source_start_seconds"],
                            "source_end_seconds": selection["source_end_seconds"],
                            "target_start_seconds": selection["target_start_seconds"],
                            "target_end_seconds": selection["target_end_seconds"],
                        },
                    }, source_audio))
                assessed = _linear_timeline_metrics(
                    target_audio, entries, [selection["source_hash"] for selection in selections])
            except (OSError, ValueError, TypeError) as exc:
                results.append({**base, "status": "not_applicable",
                                "reason": f"Comp audio analysis unavailable: {exc}"})
                continue
            if assessed is None:
                results.append({**base, "status": "not_applicable",
                                "reason": "There is insufficient non-silent comp audio for a stable comparison."})
                continue
            metrics, offsets, analysis_rate = assessed
            status = _linear_status(metrics, 0.04, 0.985)
            gains, normalized_rmse, correlation = metrics
            if status == "matched":
                reason = "The edited take is reconstructed from the declared CompSheet selections within tolerance."
            elif status == "contradicted":
                reason = "The edited take conflicts with the declared CompSheet selection timeline."
            else:
                reason = "The CompSheet fit is inconclusive; crossfades or undocumented processing may be present."
            results.append({**base, "status": status, "reason": reason,
                            "verified_scope": "comp_sheet_selections",
                            "selection_count": len(selections),
                            "gains": [round(value, 6) for value in gains],
                            "offsets_seconds": offsets,
                            "normalized_rmse": round(normalized_rmse, 6),
                            "correlation": round(correlation, 6),
                            "analysis_sample_rate_hz": analysis_rate})
        return results


def _mono_resample(audio, output_rate):
    rate = audio["sample_rate_hz"]
    count = int(len(audio["frames"]) / rate * output_rate)
    result = []
    for index in range(count):
        source_index = min(len(audio["frames"]) - 1, round(index * rate / output_rate))
        frame = audio["frames"][source_index]
        result.append(sum(frame) / len(frame))
    return result


def _correlation(left, right):
    count = min(len(left), len(right))
    if count < 32:
        return 0.0
    left, right = left[:count], right[:count]
    left_mean, right_mean = sum(left) / count, sum(right) / count
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right))
    left_energy = sum((a - left_mean) ** 2 for a in left)
    right_energy = sum((b - right_mean) ** 2 for b in right)
    denominator = math.sqrt(left_energy * right_energy)
    return numerator / denominator if denominator > 1e-12 else 0.0


def _aligned_master_samples(source, target, rate=400):
    source_samples, target_samples = _mono_resample(source, rate), _mono_resample(target, rate)
    coarse_source = source_samples[::4]
    coarse_target = target_samples[::4]
    max_lag = min(int(MAX_OFFSET_SECONDS * rate / 4),
                  max(0, min(len(coarse_source), len(coarse_target)) // 4))
    best = (float("-inf"), 0)
    for lag in range(-max_lag, max_lag + 1):
        if lag >= 0:
            left, right = coarse_source[:len(coarse_target) - lag], coarse_target[lag:]
        else:
            left, right = coarse_source[-lag:], coarse_target[:len(coarse_source) + lag]
        score = _correlation(left, right)
        if score > best[0]:
            best = score, lag * 4
    lag = best[1]
    if lag >= 0:
        source_aligned, target_aligned = source_samples[:len(target_samples) - lag], target_samples[lag:]
    else:
        source_aligned, target_aligned = source_samples[-lag:], target_samples[:len(source_samples) + lag]
    count = min(len(source_aligned), len(target_aligned))
    return source_aligned[:count], target_aligned[:count], lag / rate


def _block_rms(samples, block_size):
    return [math.sqrt(sum(value * value for value in samples[start:start + block_size]) /
                      len(samples[start:start + block_size]))
            for start in range(0, len(samples), block_size)
            if len(samples[start:start + block_size]) >= block_size // 2]


def _rms(samples):
    return math.sqrt(sum(value * value for value in samples) / len(samples)) if samples else 0.0


class MasterDerivationPass:
    """Corroborate mix-to-master continuity without claiming exact reconstruction."""

    @staticmethod
    def evaluate(native, chain):
        results = []
        for target in native["artefacts"]:
            edges = [edge for edge in target.get("evidence", [])
                     if edge["relationship_type"] == "mastered_from"]
            if not edges:
                continue
            base = _base("audio_master_content_continuity", "MasterDerivationPass",
                         target, edges)
            if target["artefact_type"] != "audio/master" or len(edges) != 1:
                results.append({**base, "status": "not_applicable",
                                "reason": "Master analysis requires one audio/mix source and one audio/master target."})
                continue
            declared_scope = (edges[0].get("attributes") or {}).get("derivation_scope")
            if declared_scope not in (None, "master_similarity"):
                results.append({**base, "status": "not_applicable",
                                "reason": "A mastered_from scope must be master_similarity when one is declared."})
                continue
            try:
                source = _read_pcm(Path(chain.artefacts[edges[0]["hash"]].get_file()))
                master = _read_pcm(Path(chain.artefacts[target["artefact_hash"]].get_file()))
                source_samples, master_samples, offset = _aligned_master_samples(source, master)
            except (OSError, ValueError, TypeError) as exc:
                results.append({**base, "status": "not_applicable",
                                "reason": f"Master comparison unavailable: {exc}"})
                continue
            if len(source_samples) < 128 or _rms(source_samples) < 1e-7 or _rms(master_samples) < 1e-7:
                results.append({**base, "status": "not_applicable",
                                "reason": "There is insufficient non-silent audio for master comparison."})
                continue
            waveform_correlation = _correlation(source_samples, master_samples)
            source_envelope = _block_rms(source_samples, 100)
            master_envelope = _block_rms(master_samples, 100)
            envelope_correlation = _correlation(source_envelope, master_envelope)
            source_delta = _rms([right - left for left, right in zip(source_samples, source_samples[1:])])
            master_delta = _rms([right - left for left, right in zip(master_samples, master_samples[1:])])
            source_brightness = source_delta / max(_rms(source_samples), 1e-12)
            master_brightness = master_delta / max(_rms(master_samples), 1e-12)
            spectral_balance_delta = abs(math.log(max(master_brightness, 1e-12) /
                                                  max(source_brightness, 1e-12)))
            spectral_similarity = math.exp(-2 * spectral_balance_delta)
            duration_source = len(source["frames"]) / source["sample_rate_hz"]
            duration_master = len(master["frames"]) / master["sample_rate_hz"]
            duration_delta = duration_master - duration_source
            loudness_delta_db = 20 * math.log10(_rms(master_samples) / _rms(source_samples))
            if (abs(duration_delta) <= 2.0 and spectral_similarity >= 0.7 and
                    ((waveform_correlation >= 0.65 and envelope_correlation >= 0.75) or
                     waveform_correlation >= 0.9)):
                status = "corroborated"
                reason = "Waveform, dynamics, timing and coarse spectral continuity support the declared mix-to-master relationship."
            elif (abs(duration_delta) > max(5.0, duration_source * 0.1) or
                  (waveform_correlation < 0.15 and envelope_correlation < 0.25 and
                   spectral_similarity < 0.4)):
                status = "contradicted"
                reason = "The submitted mix and master lack the minimum content continuity expected after mastering."
            else:
                status = "not_applicable"
                reason = "The comparison is inconclusive for real-world mastering; no exact derivation claim is made."
            results.append({**base, "status": status, "reason": reason,
                            "verified_scope": "content_continuity_only",
                            "declared_scope": declared_scope,
                            "content_relationship_verified": None,
                            "content_continuity_corroborated": status == "corroborated",
                            "offset_seconds": round(offset, 6),
                            "duration_delta_seconds": round(duration_delta, 6),
                            "waveform_correlation": round(waveform_correlation, 6),
                            "envelope_correlation": round(envelope_correlation, 6),
                            "spectral_similarity": round(spectral_similarity, 6),
                            "loudness_delta_db": round(loudness_delta_db, 4)})
        return results
