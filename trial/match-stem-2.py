#!/usr/bin/env python3
"""
stem_match.py

Check whether full-length backing stems (drums.wav, bass.wav, guitar.wav...)
are present in a finished mix (mp3 or wav) -- e.g. verifying stems from a
DAW session actually made it into the bounced/mastered version.

This is a DIFFERENT problem from sample_match.py. A "sample" is a short
snippet that may appear anywhere, pitch-shifted or time-stretched, inside a
song. A "stem" is a full-length track from the SAME session as the mix --
it should already be at the same pitch and tempo as the song, just possibly
offset by a little lead-in/lead-out padding, and it will never be a perfect
match because the finished mix has EQ, compression, other instruments, and
mastering effects layered on top. So instead of searching all 12 pitch
shifts and 5 tempo ratios (slow, and wrong for this use case), this script:

1. Finds the best small time offset (handles a few seconds of silence/
   padding difference between the stem export and the final bounce).
2. At that offset, computes how well the stem's energy/timbre correlates
   with the mix over time -- and importantly, HOW MUCH OF THE STEM's
   audible content is present, since a quiet or short stem (e.g. a single
   guitar swell) will correlate weakly overall even if it's genuinely in
   the mix, simply because most of its frames are silence.

Usage
-----
    python stem_match.py full_song.mp3 stem_drums.wav stem_bass.wav ...

Requires: librosa, soundfile, numpy
    pip install librosa soundfile numpy --break-system-packages
"""

import argparse
import sys
from dataclasses import dataclass

import numpy as np
import librosa

SR = 22050
HOP_LENGTH = 1024
MAX_OFFSET_SEC = 5.0     # search +/- this many seconds for lead-in/lead-out padding
SILENCE_DB = -45.0        # frames of the stem quieter than this are treated as "stem not playing"


def is_percussive(stem_aligned: np.ndarray) -> bool:
    """
    Rough classifier: does this stem's onset-strength envelope look spiky
    (drums/percussion) or smooth (bass, pads, held guitar/vocal notes)?
    Determines which signal (onset timing vs spectral correlation) to trust
    for the verdict -- testing showed spectral correlation alone is
    unreliable for percussive content specifically.
    """
    onset_env = librosa.onset.onset_strength(y=stem_aligned, sr=SR, hop_length=256)
    if onset_env.mean() < 1e-9:
        return False
    cv = onset_env.std() / onset_env.mean()
    return cv > 2.0


@dataclass
class StemResult:
    stem_name: str
    offset_sec: float
    overall_correlation: float   # 0-1, mel-spectrogram similarity averaged over ALL frames
    active_correlation: float    # 0-1, same but only over frames where the stem is actually playing
    active_fraction: float       # 0-1, how much of the stem's duration has audible content
    onset_correlation: float     # -1..1ish, hit-timing correlation -- the strong signal for percussive stems
    percussive: bool             # whether this stem was classified as percussive (drums-like)


def load_audio(path: str) -> np.ndarray:
    y, _ = librosa.load(path, sr=SR, mono=True)
    return y


def mel_db(y: np.ndarray) -> np.ndarray:
    S = librosa.feature.melspectrogram(y=y, sr=SR, n_mels=64, hop_length=HOP_LENGTH)
    return librosa.power_to_db(S, ref=1.0)


def find_best_offset(song_y: np.ndarray, stem_y: np.ndarray) -> float:
    """
    Cross-correlate the two signals' amplitude envelopes over a small window
    to find the best small-time-offset alignment (handles lead-in/lead-out
    padding differences between a raw stem export and the final bounce).
    No pitch-shift/time-stretch search: stems from the same session should
    already share the mix's pitch and tempo.
    """
    from scipy.signal import fftconvolve

    hop = 256
    song_env = librosa.feature.rms(y=song_y, hop_length=hop)[0]
    stem_env = librosa.feature.rms(y=stem_y, hop_length=hop)[0]
    song_env = song_env - song_env.mean()
    stem_env = stem_env - stem_env.mean()

    corr = fftconvolve(song_env, stem_env[::-1], mode="full")
    # index of zero-offset alignment in 'full' mode:
    zero_idx = len(stem_env) - 1
    max_shift_frames = int(MAX_OFFSET_SEC * SR / hop)
    lo = max(0, zero_idx - max_shift_frames)
    hi = min(len(corr), zero_idx + max_shift_frames + 1)
    window = corr[lo:hi]
    best_rel = int(np.argmax(window)) + lo - zero_idx
    offset_sec = best_rel * hop / SR
    return offset_sec


def onset_correlation(song_aligned: np.ndarray, stem_aligned: np.ndarray) -> float:
    """
    Correlate onset-strength envelopes (hit-timing) rather than spectral
    shape. This is a much more specific signal for percussive/transient
    content: mel-spectrogram correlation can be fooled by two DIFFERENT
    drum parts that just share similar tempo/broadband character (confirmed
    by testing), but if a drum stem is truly the one used in the mix, its
    individual hit timings should line up closely with the mix's onsets --
    something an unrelated (even similar-tempo) drum part won't do.
    For smooth/sustained material (bass pads, held guitar) this metric is
    less informative since there are few sharp onsets to correlate; treat it
    as a bonus signal, not a replacement for the mel correlation above.
    """
    hop = 256
    o1 = librosa.onset.onset_strength(y=song_aligned, sr=SR, hop_length=hop)
    o2 = librosa.onset.onset_strength(y=stem_aligned, sr=SR, hop_length=hop)
    m = min(len(o1), len(o2))
    if m < 4:
        return 0.0
    o1, o2 = o1[:m], o2[:m]
    if o1.std() < 1e-9 or o2.std() < 1e-9:
        return 0.0
    o1n = (o1 - o1.mean()) / o1.std()
    o2n = (o2 - o2.mean()) / o2.std()
    return float(np.clip(np.dot(o1n, o2n) / m, -1.0, 1.0))


def analyze_stem(song_y: np.ndarray, stem_y: np.ndarray, stem_name: str) -> StemResult:
    offset_sec = find_best_offset(song_y, stem_y)
    offset_samples = int(round(offset_sec * SR))

    if offset_samples >= 0:
        song_aligned = song_y[offset_samples:offset_samples + len(stem_y)]
        stem_aligned = stem_y[:len(song_aligned)]
    else:
        stem_aligned = stem_y[-offset_samples:]
        song_aligned = song_y[:len(stem_aligned)]
        stem_aligned = stem_aligned[:len(song_aligned)]

    n = min(len(song_aligned), len(stem_aligned))
    song_aligned, stem_aligned = song_aligned[:n], stem_aligned[:n]
    if n < SR:  # less than 1 second of overlap -- can't say anything meaningful
        return StemResult(stem_name, offset_sec, 0.0, 0.0, 0.0, 0.0)

    mel_song = mel_db(song_aligned)
    mel_stem = mel_db(stem_aligned)
    m = min(mel_song.shape[1], mel_stem.shape[1])
    mel_song, mel_stem = mel_song[:, :m], mel_stem[:, :m]

    # per-frame RMS (in dB) of the stem, to know which frames it's actually playing in
    stem_rms_db = librosa.power_to_db(
        librosa.feature.rms(y=stem_aligned, hop_length=HOP_LENGTH)[0] ** 2, ref=1.0
    )[:m]
    active_mask = stem_rms_db > SILENCE_DB
    active_fraction = float(active_mask.mean()) if m > 0 else 0.0

    a = mel_song - mel_song.mean(axis=0, keepdims=True)
    b = mel_stem - mel_stem.mean(axis=0, keepdims=True)
    num = (a * b).sum(axis=0)
    den = np.linalg.norm(a, axis=0) * np.linalg.norm(b, axis=0) + 1e-9
    frame_sims = np.clip(num / den, -1.0, 1.0)
    # rescale from [-1,1] to [0,1] for readability
    frame_sims01 = (frame_sims + 1.0) / 2.0

    overall_correlation = float(frame_sims01.mean())
    if active_mask.any():
        active_correlation = float(frame_sims01[active_mask].mean())
    else:
        active_correlation = 0.0

    onset_corr = onset_correlation(song_aligned, stem_aligned)
    percussive = is_percussive(stem_aligned)

    return StemResult(stem_name, offset_sec, overall_correlation, active_correlation,
                       active_fraction, onset_corr, percussive)


def main():
    parser = argparse.ArgumentParser(description="Check whether full-length stems are present in a finished mix.")
    parser.add_argument("song", help="Path to the finished song/mix (mp3 or wav)")
    parser.add_argument("stems", nargs="+", help="Path(s) to candidate stem wav files")
    parser.add_argument("--threshold", type=float, default=0.65,
                         help="Active-correlation (0-1) above which a stem is reported PRESENT (default 0.65)")
    args = parser.parse_args()

    print(f"Loading song: {args.song}")
    song_y = load_audio(args.song)
    print(f"  Duration: {len(song_y)/SR:.1f}s\n")

    results = []
    for stem_path in args.stems:
        print(f"Analyzing stem: {stem_path} ...")
        stem_y = load_audio(stem_path)
        results.append(analyze_stem(song_y, stem_y, stem_path))

    print("\n" + "=" * 70)
    print("RESULTS")
    print("=" * 70)
    for r in sorted(results, key=lambda r: -r.active_correlation):
        if r.percussive:
            # Spectral correlation alone is unreliable for percussive stems
            # (confirmed by testing -- unrelated drum parts at similar tempo
            # still score high). Onset-timing correlation is the trustworthy
            # signal here.
            is_match = r.onset_correlation >= 0.4
            uncertain = 0.25 <= r.onset_correlation < 0.4
            basis = "onset correlation (percussive stem)"
        else:
            is_match = r.active_correlation >= args.threshold
            uncertain = (args.threshold - 0.1) <= r.active_correlation < args.threshold
            basis = "spectral correlation"

        verdict = "PRESENT in mix" if is_match else ("UNCERTAIN -- borderline, check manually" if uncertain else "NOT found / weak")

        print(f"\n{r.stem_name}")
        print(f"  Verdict:             {verdict}  (basis: {basis})")
        print(f"  Active correlation:  {r.active_correlation*100:5.1f}%  (spectral similarity during the stem's audible parts)")
        print(f"  Overall correlation: {r.overall_correlation*100:5.1f}%  (spectral similarity across the WHOLE stem, incl. silent parts)")
        print(f"  Onset correlation:   {r.onset_correlation:+.2f}    (hit-timing match; classified as {'PERCUSSIVE -- this is the trustworthy metric for this stem' if r.percussive else 'smooth/sustained -- not very informative for this stem'})")
        print(f"  Stem active:         {r.active_fraction*100:5.1f}% of its duration")
        print(f"  Best alignment:      {r.offset_sec:+.2f}s offset vs. the song")

    print("\nNote: spectral correlation alone can be fooled by unrelated stems that happen to share tempo/")
    print("broadband character (confirmed by testing) -- this is a bigger risk for drums/percussion than for")
    print("melodic or vocal parts, which is why percussive stems above are judged by onset-timing correlation")
    print("instead. If a non-percussive result still looks uncertain, run a stem you KNOW isn't in the song as")
    print("a personal baseline for comparison -- real vs. unrelated scores should separate clearly for your mix.")


if __name__ == "__main__":
    sys.exit(main())
