"""Synthetic board lines for tests: static echoes + noise, optionally a breathing target."""
import numpy as np


def make_lines(seconds=20.0, target_bin=9, breaths_per_min=40.0, amp_mm=2.0, target_amp=800.0,
               lose=(), seed=0, n=24, scale=10):
    """Returns board lines like the real firmware (C line, then D lines every 60 ms).

    target_bin=None gives an empty room. Bin 9 = 15 + 9 * 3.75 = 48.75 cm.
    """
    rng = np.random.default_rng(seed)
    static = rng.normal(0, 300, n) + 1j * rng.normal(0, 300, n)
    out = ["S,0,ibuki fw start", f"C,15.0,37.5,{n},{scale}"]
    for i in range(int(seconds / 0.06)):
        if i in lose:
            continue
        t_ms = i * 60
        z = static + rng.normal(0, 5, n) + 1j * rng.normal(0, 5, n)
        if target_bin is not None:
            d = amp_mm * np.sin(2 * np.pi * breaths_per_min / 60.0 * t_ms / 1000.0)
            z[target_bin] += target_amp * np.exp(1j * 4 * np.pi * d / 5.0)
        iq = np.round(z * scale)
        vals = ",".join(f"{int(a)},{int(b)}" for a, b in zip(iq.real, iq.imag))
        out.append(f"D,{i},{t_ms},{vals}")
    return out
