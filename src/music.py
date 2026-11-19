"""Copyright-free background music, synthesised from scratch (no samples, no licence issues).

Soft ambient/lo-fi bed: pad chords + gentle plucked arpeggio + light pulse.
A different key / progression is picked every day from the date.
"""
import wave
import zlib
from pathlib import Path

import numpy as np

SR = 44100
PROGRESSIONS = [  # semitone offsets from the root, minor-ish "tech" moods
    [(0, 3, 7, 10), (-4, 0, 3, 7), (-7, -3, 0, 5), (-2, 2, 5, 9)],   # i7 – VI – iv – VII
    [(0, 3, 7, 12), (5, 8, 12, 15), (-4, 0, 3, 7), (-2, 2, 5, 10)],  # i – iv – VI – VII
    [(0, 4, 7, 11), (-3, 0, 4, 7), (-8, -5, -1, 4), (-5, -1, 2, 7)],  # Imaj7 – vi – IV – V
]
ROOTS = [57, 55, 53, 52, 50]  # A3, G3, F3, E3, D3 (MIDI)


def _hz(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def generate(seconds: float, out: Path, seed_text: str = "", bpm: int = 96):
    seed = zlib.crc32(seed_text.encode())
    rng = np.random.default_rng(seed)
    prog = PROGRESSIONS[seed % len(PROGRESSIONS)]
    root = ROOTS[(seed // 7) % len(ROOTS)]
    n = int(SR * (seconds + 1))
    t = np.arange(n) / SR
    beat = 60 / bpm
    bar = beat * 4
    mix = np.zeros(n)

    # pad: detuned saws, slow attack, one chord per 2 bars
    chord_len = bar * 2
    for ci in range(int(seconds / chord_len) + 2):
        chord = prog[ci % len(prog)]
        s0 = int(ci * chord_len * SR)
        L = int(chord_len * 1.15 * SR)
        if s0 >= n:
            break
        L = min(L, n - s0)
        tt = np.arange(L) / SR
        env = np.minimum(1, tt / 0.8) * np.minimum(1, (L / SR - tt) / 0.6)
        seg = np.zeros(L)
        for k, iv in enumerate(chord):
            f = _hz(root + iv)
            for det in (-0.12, 0.12):
                ph = 2 * np.pi * f * (1 + det / 100) * tt + rng.uniform(0, 6.28)
                seg += 0.5 * np.sin(ph) + 0.18 * np.sin(2 * ph) + 0.06 * np.sin(3 * ph)
        mix[s0:s0 + L] += 0.06 * seg * env

    # arpeggio: plucked sines on 8th notes, one octave up
    step = beat / 2
    pattern = [0, 2, 1, 3, 2, 1, 3, 2]
    for si in range(int(seconds / step) + 1):
        chord = prog[int(si * step / chord_len) % len(prog)]
        note = root + 12 + chord[pattern[si % len(pattern)] % len(chord)]
        s0 = int(si * step * SR)
        L = min(int(0.6 * SR), n - s0)
        if L <= 0:
            break
        tt = np.arange(L) / SR
        f = _hz(note)
        env = np.exp(-tt * 7) * np.minimum(1, tt / 0.005)
        vel = 0.75 + 0.25 * rng.random()
        mix[s0:s0 + L] += 0.09 * vel * env * (np.sin(2 * np.pi * f * tt) + 0.25 * np.sin(4 * np.pi * f * tt))

    # soft pulse (filtered kick) on beats 1 and 3, enters after the hook
    for bi in range(int(seconds / beat) + 1):
        if bi % 2 or bi * beat < 4:
            continue
        s0 = int(bi * beat * SR)
        L = min(int(0.25 * SR), n - s0)
        if L <= 0:
            break
        tt = np.arange(L) / SR
        f = 55 * np.exp(-tt * 18) + 42
        mix[s0:s0 + L] += 0.22 * np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-tt * 14)

    # fade in / out
    fade = int(1.5 * SR)
    mix[:fade] *= np.linspace(0, 1, fade)
    mix[-fade:] *= np.linspace(1, 0, fade)
    mix = mix / (np.max(np.abs(mix)) + 1e-9) * 0.5
    stereo = np.stack([mix, np.roll(mix, int(0.012 * SR))], axis=1)  # tiny Haas widening
    pcm = (stereo * 32767).astype(np.int16)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return out
