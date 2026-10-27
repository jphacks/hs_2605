"""A second model that decides the baby's state over time, in place of hand-written alarm rules.

The breathing model (ml/train.py) answers, every 0.5 s and for each distance slot, "is
something breathing here?". This model looks at the last 30 s of those answers for all 24
slots, with three more measurements per slot (how much the echo moves, how fast its strength
changes, how strong it is), and says what the baby is doing now:
    0 breathing   1 stopped   2 moving   3 empty
30 s is longer than the breathing model's own window, so this model still sees that a body was
breathing a moment ago when it stops: that, and the echo staying where it was, is what tells
"stopped" apart from "nobody there".

The network treats every distance the same way (2D convolutions over time x distance, then the
strongest evidence over all distances): a baby at 30 cm and one at 60 cm look alike to it, and
it never has to decide which slot is the baby.

The alarm is then just the setting: "stopped" without a break for no_breath_s means an alert
(the count starts LAG_S earlier, as the model only says "stopped" some seconds after the last
breath). "Empty" for LEFT_S after someone was there is reported like the Brain's left_bed, so a
stopped baby that the model takes for "nobody there" still reaches the parents.

Training data. Train sets only; the test babies, rooms and people stay unseen.
  simulated sessions (ml/synth.py)     breathing: breathed < 5 s ago, stopped: not for >= 8 s
  real recordings (pc/record_session.py), ramadhan and lemonwasp:
      rec_breathing_*   breathing                 sitting still, 20, 30 or 50 cm
      rec_hold_*        breathing, then stopped   breath held from 20 s to 40 s (the cues)
      rec_moving_*      moving                    moving the whole minute
      rec_shake_*       not stopped, not empty    breathing, the table bumped twice
      rec_empty_*       empty                     nobody in the room
      rec_leave_*       breathing, then empty     walks away at 20 s
      (also very light breathing and breathing under a blanket, recorded on 2026-10-28)
  light-breathing copies of the breathing and hold recordings: the chest moves only 35% as
      much (LIGHT_GAIN), for people and babies who breathe very lightly
  a long real recording of an adult working beside the board (recordings/room/): nobody
      stops breathing in it, so it is "anything but stopped". The first ROOM_TRAIN_S seconds
      train, the rest is a real false-alarm check for choosing settings.
Labels that allow several states ("not stopped") are trained with a partial-label loss: the
model only has to put its probability somewhere inside the allowed set.

Run with the AI environment, after ml.train:
    ../ml-env/bin/python -m ml.state_model           # train, score on validation, write the 8-bit model
    ../ml-env/bin/python -m ml.state_model --val     # score again on validation, no training
    ../ml-env/bin/python -m ml.state_model --test    # once: the unseen test set and real recordings
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from ml import evaluate
from ml.features import NUM_BINS, STEP_CM, read_csv, windows

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "ml" / "data"
MODELS = DATA / "models"
CACHE = DATA / "cache"           # the breathing model's answers per recording, computed once
RECORDINGS = REPO / "recordings"

HOP = 8          # lines between steps: ~0.5 s, as the breathing model runs
SEQ = 60         # steps the model looks back: ~30 s
FEATURES = 4     # per slot and step: P(breathing), movement, strength change, echo level
STATES = ["breathing", "stopped", "moving", "empty"]
LAG_S = 6.0      # "stopped" is learnt as "no breath for >= 8 s"; counting from 8 s back rings right
                 # on the setting but with twice the false alarms (validation, co-sleeping, 10 s:
                 # 4.5 vs 2.3 per hour; the real room: 4 vs 1). From 6 s back it rings ~2 s later
LEFT_S = 5.0     # "empty" this long after someone was there: left_bed
TRAIN_PEOPLE = ("ramadhan", "lemonwasp")
TEST_PEOPLE = ("manami",)
ROOM_TRAIN = "rec_room_ambient_20261027-220928.csv"   # the first long room recording
ROOM_TRAIN_S = 12 * 60.0                              # its first 12 min train, the last 8 validate
LIGHT_GAIN = 0.35  # light-breathing copies of the real breathing recordings (see dataset())
SYNTH_STRIDE = 2  # simulated sessions: every 2nd step is enough (neighbouring steps overlap 98%)

# a label is the set of states the model may answer (one-hot, or several for "not stopped")
B, S, M, E = np.eye(len(STATES), dtype=np.float32)
NOT_STOPPED = B + M + E
NONE = np.zeros(len(STATES), np.float32)              # unknown: not trained on


def steps(breath_model, csv: Path, gain: float = 1.0) -> dict:
    """Every ~0.5 s of one recording: the end line, its time and the model inputs (24 x 4).

    Cached in ml/data/cache/ until the breathing model changes.
    """
    tag = "" if gain == 1.0 else f"_gain{gain:g}"
    key = CACHE / ("_".join(csv.resolve().relative_to(REPO).with_suffix("").parts) + tag + ".npz")
    newest = max((MODELS / "breath_slot.keras").stat().st_mtime, csv.stat().st_mtime)
    if key.exists() and key.stat().st_mtime > newest:
        return dict(np.load(key))
    t_ms, z = read_csv(csv)
    if gain != 1.0:
        # lighter breathing: each slot's movement around its still echo, made smaller
        still = z.mean(axis=0)
        z = still + (z - still) * gain
    # each slot's echo-strength change from one line to the next (the Brain's movement signal)
    change = np.vstack([np.zeros((1, NUM_BINS)), np.abs(np.diff(np.abs(z), axis=0))])
    ends, feats, move, chg, level = [], [], [], [], []
    for end, f in windows(t_ms, z, hop=HOP):
        ends.append(end)
        feats.append(f)
        last3 = z[max(0, end - 49):end + 1]                       # ~3 s of lines
        move.append(np.abs(last3 - last3.mean(axis=0)).mean(axis=0))
        chg.append(change[max(0, end - 16):end + 1].mean(axis=0))  # ~1 s, the Brain's 17 lines
        level.append(np.abs(last3.mean(axis=0)))                  # the still echo: a body, a wall
    p = breath_model.predict(np.concatenate(feats), batch_size=4096, verbose=0)[:, 0]
    x = np.stack([p.reshape(len(ends), NUM_BINS),
                  np.log10(1.0 + np.array(move)) / 3.0,           # all four roughly 0..1
                  np.log10(1.0 + np.array(chg)) / 2.0,
                  np.log10(1.0 + np.array(level)) / 3.0], axis=-1).astype(np.float32)
    ends = np.array(ends)
    out = {"ends": ends, "t_ms": t_ms[ends], "rel_s": (t_ms[ends] - t_ms[0]) / 1000.0, "x": x}
    CACHE.mkdir(parents=True, exist_ok=True)
    np.savez(key, **out)
    return out


def sequences(x: np.ndarray, rows: np.ndarray | None = None) -> np.ndarray:
    """The last SEQ steps before each step (zeros before the recording started): (n, SEQ, 24, 4)."""
    pad = np.concatenate([np.zeros((SEQ - 1,) + x.shape[1:], np.float32), x])
    rows = np.arange(len(x)) if rows is None else rows
    return np.stack([pad[i:i + SEQ] for i in rows])


def synth_labels(truth: dict, st: dict) -> np.ndarray:
    since = np.array(truth["since_breath_s"], float)[st["ends"]]
    y = np.tile(NONE, (len(since), 1))
    y[since < 5.0] = B
    y[since >= 8.0] = S
    return y


def real_labels(csv: Path, st: dict) -> np.ndarray | None:
    """Labels from what each recording was (its name) and its spoken cues (seconds from the start)."""
    rel, x, n = st["rel_s"], st["x"], len(st["ends"])
    y = np.tile(NONE, (n, 1))
    name = csv.name
    if name.startswith("rec_breathing_"):
        y[:] = B
    elif name.startswith("rec_moving_"):
        y[:] = M
    elif name.startswith("rec_empty_"):
        y[:] = E
    elif name.startswith(("rec_shake_", "rec_room_")):
        y[:] = NOT_STOPPED
    elif name.startswith("rec_hold_"):
        # "hold your breath" at 20 s, "breathe again" at 40 s; ~1 s to react
        y[rel < 19.0] = B
        y[(rel >= 29.0) & (rel < 40.0)] = S
        y[rel >= 46.0] = B
    elif name.startswith("rec_leave_"):
        # "walk away" at 20 s. Empty once nothing from 28 cm on has moved like a person
        # (25, an empty room's most) for 3 s; before the cue, breathing
        y[rel < 19.0] = B
        cm = 15.0 + np.arange(NUM_BINS) * STEP_CM
        move = 10 ** (x[:, cm >= 28.0, 1] * 3.0) - 1.0
        run = 0
        for i in range(n):
            run = run + 1 if move[i].max() < 25.0 else 0
            if rel[i] >= 22.0 and run >= 6:
                y[i] = E
    else:
        return None
    return y


def real_files(people) -> list[Path]:
    return [f for p in people for f in sorted((RECORDINGS / p).glob("rec_*.csv"))]


def dataset(breath_model, synth_set: str, people, room: str) -> tuple[np.ndarray, np.ndarray]:
    """Inputs and labels: simulated sessions of one set, the people's real recordings, and part of
    the long room recording ("train": its first ROOM_TRAIN_S, "val": the rest, "": none)."""
    X, Y = [], []

    def add(st, y, rows):
        rows = rows[y[rows].sum(axis=1) > 0]       # unknown steps are left out
        X.append(sequences(st["x"], rows))
        Y.append(y[rows])

    for csv in sorted((DATA / synth_set).glob("*/*.csv")):
        st = steps(breath_model, csv)
        add(st, synth_labels(json.loads(csv.with_suffix(".json").read_text()), st),
            np.arange(0, len(st["ends"]), SYNTH_STRIDE))
    for csv in real_files(people):
        st = steps(breath_model, csv)
        y = real_labels(csv, st)
        if y is not None:
            add(st, y, np.arange(len(y)))
        if y is not None and csv.name.startswith(("rec_breathing_", "rec_hold_")):
            # the same recording with the chest moving only LIGHT_GAIN as much: people (and
            # babies) who breathe very lightly. The labels stay: breathing is still breathing
            st = steps(breath_model, csv, LIGHT_GAIN)
            add(st, real_labels(csv, st), np.arange(len(st["ends"])))
    path = RECORDINGS / "room" / ROOM_TRAIN
    if room and path.exists():
        st = steps(breath_model, path)
        part = st["rel_s"] < ROOM_TRAIN_S if room == "train" else st["rel_s"] >= ROOM_TRAIN_S + 30.0
        add(st, np.where(part[:, None], NOT_STOPPED, NONE), np.arange(len(part)))
    return np.concatenate(X), np.concatenate(Y)


def partial_loss(allowed, probs):
    """-log of the probability the model gives to the allowed states (plain cross-entropy for one)."""
    import tensorflow as tf
    return -tf.math.log(tf.reduce_sum(allowed * probs, axis=-1) + 1e-7)


def build():
    from tensorflow import keras
    from tensorflow.keras import layers
    inp = keras.Input((SEQ, NUM_BINS, FEATURES))
    # time x distance: 5 steps (2.5 s) by 3 slots (11 cm), halving the time each layer
    x = layers.Conv2D(16, (5, 3), strides=(2, 1), padding="same", activation="relu")(inp)
    x = layers.Conv2D(24, (5, 3), strides=(2, 1), padding="same", activation="relu")(x)
    x = layers.Conv2D(32, (5, 3), strides=(2, 1), padding="same", activation="relu")(x)
    # the strongest evidence over all distances, kept apart per moment (8 x ~4 s)
    x = layers.MaxPooling2D((1, NUM_BINS))(x)
    # a fixed size, not Flatten: Flatten works its size out at run time, which the board's
    # AI chip (Ethos-U55) can't do. 60 steps halved 3 times = 8, times 32 filters
    x = layers.Reshape((8 * 32,))(x)
    x = layers.Dense(16, activation="relu")(x)
    out = layers.Dense(len(STATES), activation="softmax")(x)
    m = keras.Model(inp, out)
    m.compile(optimizer=keras.optimizers.Adam(1e-3), loss=partial_loss)
    return m


def label_weights(Y: np.ndarray) -> np.ndarray:
    """Each kind of label counts about equally (at most 10x per example): stopped and empty are rarer."""
    kinds, idx, counts = np.unique(Y, axis=0, return_inverse=True, return_counts=True)
    w = np.minimum(10.0, len(Y) / (len(kinds) * counts))
    return w[idx.ravel()].astype(np.float32)


class Alarm:
    """The only rules: "stopped" without a break for no_breath_s (counted from LAG_S back) is an
    alert; "empty" for LEFT_S after someone was there is left_bed."""

    def __init__(self, no_breath_s: float):
        self.no_breath_s = no_breath_s
        self.stopped_since = None
        self.empty_since = None
        self.present = False
        self.alerted = False

    def feed(self, t: float, state: int) -> list[dict]:
        events = []
        if state == 3:
            self.empty_since = t if self.empty_since is None else self.empty_since
            if self.present and t - self.empty_since >= LEFT_S:
                events.append({"type": "alert", "reason": "left_bed"})
                self.present = False
        else:
            self.empty_since = None
            self.present = True
        if state != 1:
            self.stopped_since, self.alerted = None, False
            return events
        if self.stopped_since is None:
            self.stopped_since = t - LAG_S
        if not self.alerted and t - self.stopped_since >= self.no_breath_s:
            self.alerted = True
            events.append({"type": "alert", "reason": "no_breath",
                           "since_s": int(round(t - self.stopped_since))})
        return events


def npu_int8(sample: np.ndarray) -> bytes:
    """The trained state model as an 8-bit file for the board's AI chip.

    Models trained before the Reshape change used Flatten; this rebuilds the same network with
    the fixed-size Reshape and copies the trained weights over, so the answers stay the same.
    """
    from tensorflow import keras
    from ml.train import to_int8_tflite
    trained = keras.models.load_model(MODELS / "state.keras", custom_objects={"partial_loss": partial_loss})
    fixed = build()
    fixed.set_weights(trained.get_weights())
    return to_int8_tflite(fixed, sample)


def int8_probs(blob: bytes, X: np.ndarray) -> np.ndarray:
    """State probabilities from the 8-bit model, to check it still agrees with the full model."""
    import tensorflow as tf
    it = tf.lite.Interpreter(model_content=blob)
    it.allocate_tensors()
    i, o = it.get_input_details()[0], it.get_output_details()[0]
    (si, zi), (so, zo) = i["quantization"], o["quantization"]
    out = np.empty((len(X), len(STATES)), np.float32)
    for n, x in enumerate(X):
        it.set_tensor(i["index"], np.clip(np.round(x / si + zi), -128, 127).astype(np.int8)[None])
        it.invoke()
        out[n] = (it.get_tensor(o["index"])[0].astype(np.float32) - zo) * so
    return out


def predict_states(state_model, st: dict) -> np.ndarray:
    return state_model.predict(sequences(st["x"]), batch_size=1024, verbose=0).argmax(axis=1)


def run_session(breath_model, state_model, csv: Path, no_breath_s: float) -> dict:
    """Both models and the alarm on one recording; results per D line, like evaluate.replay."""
    t_ms, _ = read_csv(csv)
    st = steps(breath_model, csv)
    alarm = Alarm(no_breath_s)
    names = {0: "STILL", 1: "CHECK", 2: "OK", 3: "EMPTY"}
    per_line, events = ["EMPTY"] * len(t_ms), []
    ends = st["ends"]
    for n, s in enumerate(predict_states(state_model, st)):
        for ev in alarm.feed(float(st["t_ms"][n]) / 1000.0, int(s)):
            events.append({"t_ms": int(st["t_ms"][n]), **ev})
        label = "ALERT" if alarm.alerted else names[int(s)]
        stop = ends[n + 1] if n + 1 < len(ends) else len(t_ms)
        per_line[ends[n]:stop] = [label] * (stop - ends[n])   # held until the next step
    return {"states": per_line, "chests": [None] * len(t_ms), "bpms": [None] * len(t_ms), "events": events}


def score_set(breath_model, state_model, folder: Path, no_breath_s: float) -> list[dict]:
    """Simulated sessions: stops caught, delay, false alarms, as ml.decide reports the rules."""
    from ml.decide import catchable
    rows = []
    for kind in ("alone", "cosleep"):
        res, can = [], []
        for f in sorted((folder / kind).glob("*.csv")):
            truth = json.loads(f.with_suffix(".json").read_text())
            res.append(evaluate.score(truth, run_session(breath_model, state_model, f, no_breath_s), no_breath_s))
            can.append(catchable(truth, no_breath_s))
        row = evaluate.report(kind, no_breath_s, res)
        row["catchable_caught"] = f"{sum(1 for r, c in zip(res, can) if c and r['caught'])}/{sum(can)}"
        rows.append(row)
        print({k: row.get(k) for k in ("kind", "no_breath_s", "catchable_caught", "median_delay_s",
                                       "false_alarms_per_hour", "pause_alarms", "left_bed_wrong")})
    return rows


def real_check(breath_model, state_model, csv: Path, no_breath_s: float, from_s: float = 0.0) -> dict:
    """A real recording: alarms per kind, and how often each state was chosen. In a hold recording
    one no_breath alert during the hold is right; everywhere else every no_breath alert is false."""
    st = steps(breath_model, csv)
    keep = st["rel_s"] >= from_s
    states = predict_states(state_model, st)[keep]
    alarm, events = Alarm(no_breath_s), []
    for t, s, r in zip(st["t_ms"][keep], states, st["rel_s"][keep]):
        events += [dict(ev, rel_s=round(float(r), 1)) for ev in alarm.feed(float(t) / 1000.0, int(s))]
    minutes = (st["rel_s"][keep][-1] - st["rel_s"][keep][0]) / 60.0
    return {"file": csv.name, "minutes": round(float(minutes), 1), "no_breath_s": no_breath_s,
            "no_breath_at_s": [e["rel_s"] for e in events if e["reason"] == "no_breath"],
            "left_bed_at_s": [e["rel_s"] for e in events if e["reason"] == "left_bed"],
            "states_pct": {STATES[k]: round(100.0 * float(np.mean(states == k)), 1) for k in range(len(STATES))}}


def main():
    global LAG_S
    ap = argparse.ArgumentParser(description="Train and score the state model.")
    ap.add_argument("--val", action="store_true", help="score on validation without training")
    ap.add_argument("--test", action="store_true", help="once: the unseen test set and real recordings")
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--npu", action="store_true", help="write the 8-bit model for the board (state_int8.tflite)")
    ap.add_argument("--lag", type=float, help=f"seconds the count starts back (default {LAG_S:g})")
    args = ap.parse_args()
    if args.lag is not None:
        LAG_S = args.lag
    import tensorflow as tf
    from tensorflow import keras
    tf.keras.utils.set_random_seed(9)
    breath = keras.models.load_model(MODELS / "breath_slot.keras")
    if args.npu:
        Xv, _ = dataset(breath, "val", (), "val")
        some = Xv[np.random.default_rng(9).choice(len(Xv), 500, replace=False)]
        blob = npu_int8(some)
        (MODELS / "state_int8.tflite").write_bytes(blob)
        full = keras.models.load_model(MODELS / "state.keras", custom_objects={"partial_loss": partial_loss})
        agree = float(np.mean(int8_probs(blob, some).argmax(1) == full.predict(some, verbose=0).argmax(1)))
        print({"state_int8_bytes": len(blob), "int8_agrees_with_full": round(agree, 4)})
        return
    if args.val or args.test:
        state = keras.models.load_model(MODELS / "state.keras", custom_objects={"partial_loss": partial_loss})
    else:
        X, Y = dataset(breath, "train", TRAIN_PEOPLE, "train")
        Xv, Yv = dataset(breath, "val", (), "val")
        kinds, counts = np.unique(Y, axis=0, return_counts=True)
        print("training examples:", {"+".join(STATES[i] for i in np.flatnonzero(k)): int(c)
                                     for k, c in zip(kinds, counts)}, "validation:", len(Yv))
        state = build()
        state.fit(X, Y, sample_weight=label_weights(Y), epochs=args.epochs, batch_size=256, shuffle=True,
                  validation_data=(Xv, Yv, label_weights(Yv)), verbose=2,
                  callbacks=[tf.keras.callbacks.EarlyStopping(patience=5, restore_best_weights=True)])
        state.save(MODELS / "state.keras")
        from ml.train import to_int8_tflite
        rng = np.random.default_rng(9)
        blob = to_int8_tflite(state, X[rng.choice(len(X), 500, replace=False)])
        (MODELS / "state_int8.tflite").write_bytes(blob)
        some = Xv[rng.choice(len(Xv), min(2000, len(Xv)), replace=False)]
        agree = float(np.mean(int8_probs(blob, some).argmax(1) == state.predict(some, verbose=0).argmax(1)))
        report = {"params": int(state.count_params()), "int8_bytes": len(blob),
                  "int8_agrees_with_full": round(agree, 4), "seq_steps": SEQ, "hop_lines": HOP,
                  "features": FEATURES, "states": STATES, "lag_s": LAG_S}
        (MODELS / "state.json").write_text(json.dumps(report, indent=1))
        print(report)
    folder = "test" if args.test else "val"
    for nb in (10.0, 20.0):
        score_set(breath, state, DATA / folder, nb)
        room = sorted((RECORDINGS / "room").glob("rec_room_*.csv"))
        if args.test:
            for csv in real_files(TEST_PEOPLE) + [f for f in room if f.name != ROOM_TRAIN]:
                print(real_check(breath, state, csv, nb))
        elif (RECORDINGS / "room" / ROOM_TRAIN).exists():
            print(real_check(breath, state, RECORDINGS / "room" / ROOM_TRAIN, nb, ROOM_TRAIN_S + 30.0))


if __name__ == "__main__":
    main()
