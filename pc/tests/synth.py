"""Synthetic board lines for tests: static echoes + noise, optionally a breathing target."""
import numpy as np


def make_lines(seconds=20.0, target_bin=9, breaths_per_min=40.0, amp_mm=2.0, target_amp=800.0,
               lose=(), seed=0, n=24, scale=10, shake=None, move=None, hold=None, leave_at=None,
               lighter=None):
    """Returns board lines like the real firmware (C line, then D lines every 60 ms).

    target_bin=None gives an empty room. Bin 9 = 15 + 9 * 3.75 = 48.75 cm.
    shake=(t0_s, t1_s, mm): the board moves along the beam (random walk, mm per line), so every
      echo rotates together.
    move=(t0_s, t1_s, bins): a person moving: big random changes in those bins only.
    hold=(t0_s, t1_s): breathing stops (the chest stays where it was).
    leave_at=t_s: the person is gone from then on.
    lighter=(t_s, mm): from then on the breaths are only mm deep (deep breaths, then light ones).
    """
    rng = np.random.default_rng(seed)
    static = rng.normal(0, 300, n) + 1j * rng.normal(0, 300, n)
    out = ["S,0,ibuki fw start", f"C,15.0,37.5,{n},{scale}"]
    board_mm = 0.0
    for i in range(int(seconds / 0.06)):
        if i in lose:
            continue
        t_ms = i * 60
        t_s = t_ms / 1000.0
        z = static + rng.normal(0, 5, n) + 1j * rng.normal(0, 5, n)
        if target_bin is not None and (leave_at is None or t_s < leave_at):
            t_breath = min(t_s, hold[0]) if hold and hold[0] <= t_s < hold[1] else t_s
            amp = lighter[1] if lighter and t_s >= lighter[0] else amp_mm
            d = amp * np.sin(2 * np.pi * breaths_per_min / 60.0 * t_breath)
            z[target_bin] += target_amp * np.exp(1j * 4 * np.pi * d / 5.0)
        if move and move[0] <= t_s < move[1]:
            for b in move[2]:
                z[b] += rng.normal(0, 400) + 1j * rng.normal(0, 400)
        if shake and shake[0] <= t_s < shake[1]:
            board_mm += rng.normal(0, shake[2])
        z = z * np.exp(1j * 4 * np.pi * board_mm / 5.0)
        iq = np.round(z * scale)
        vals = ",".join(f"{int(a)},{int(b)}" for a, b in zip(iq.real, iq.imag))
        out.append(f"D,{i},{t_ms},{vals}")
    return out
