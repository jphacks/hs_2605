"""Train the breathing model: "is something breathing at this distance right now?"

The model looks at one distance slot at a time (with its neighbours, see ml/features.py) and
answers yes or no. The alarm logic stays in plain rules (ml/decide.py); this model only replaces
the hard part of the Brain, recognising breathing in a noisy echo.

Data: simulated sessions from ml/synth.py. ml/data/train is for training, ml/data/test for the
final check only. The two sets use different babies (AIR-400 S01-S08 vs S09-S10) and different
rooms and people, so the test score shows how the model does on situations it has never seen.

Labels, per 12 s window and slot, from each session's truth file:
    1 (breathing)  the baby's slot when it breathed in the last 5 s; the parent's chest slot in
                   the breathing recordings (they breathe the whole minute)
    0 (no)         the baby's slot 8 s or more into a stop; slots well away from everyone
                   (walls, furniture, empty air)
    not used       the baby's slot 5-8 s after a breath (too early to say); slots right next to
                   the baby or parent (their echo spreads there); the parent's slots in the hold
                   recordings (we don't know when they held their breath)

Run with the AI environment (TensorFlow needs Python 3.11, see the work log):
    ../ml-env/bin/python -m ml.train
Writes ml/data/models/: breath_slot.keras (the trained model), breath_slot_int8.tflite (8-bit,
for the board's AI chip) and breath_slot.json (settings and test scores).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from ml.features import CHANNELS, NUM_BINS, STEP_CM, STEPS, read_csv, windows

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "ml" / "data"
MODELS = DATA / "models"

BREATHING_WITHIN_S = 5.0   # the baby breathed this recently: label 1
STOPPED_FOR_S = 8.0        # no breath for this long: label 0
FAR_SLOTS = 3              # "away from everyone" = at least this many slots from the baby...
PARENT_BODY_SLOTS = 4      # ...and this many from the parent's chest (an adult body spans ~4 slots)
FAR_PER_WINDOW = 2         # far slots sampled per window, to keep yes/no roughly balanced


def slot_of(cm: float) -> int:
    return int(round((cm - 15.0) / STEP_CM))


def examples(folder: Path, hop: int, rng) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Inputs, labels and what each example is (0 = baby, 1 = parent, 2 = far slot)."""
    X, y, what = [], [], []
    for csv in sorted(folder.glob("*/*.csv")):
        truth = json.loads(csv.with_suffix(".json").read_text())
        t_ms, z = read_csv(csv)
        since = np.array(truth["since_breath_s"], float)
        kb = slot_of(truth["baby_cm"])
        kp = None if truth["parent_cm"] is None else slot_of(truth["parent_cm"])
        parent_known = kp is not None and "rec_hold" not in truth["base"]
        far = [k for k in range(NUM_BINS)
               if abs(k - kb) >= FAR_SLOTS and (kp is None or abs(k - kp) >= PARENT_BODY_SLOTS)]
        for end, feats in windows(t_ms, z, hop=hop):
            s = since[end]
            if not np.isnan(s):
                if s < BREATHING_WITHIN_S:
                    X.append(feats[kb]); y.append(1); what.append(0)
                elif s >= STOPPED_FOR_S:
                    X.append(feats[kb]); y.append(0); what.append(0)
            if parent_known:
                X.append(feats[kp]); y.append(1); what.append(1)
            for k in rng.choice(far, size=min(FAR_PER_WINDOW, len(far)), replace=False):
                X.append(feats[k]); y.append(0); what.append(2)
    return np.array(X, np.float32), np.array(y, np.int8), np.array(what, np.int8)


def build_model():
    """A small 1D CNN. Every layer is one the board's AI chip (Ethos-U55) runs natively."""
    from tensorflow import keras
    from tensorflow.keras import layers
    inp = keras.Input((STEPS, CHANNELS))
    x = layers.Conv1D(16, 7, strides=2, padding="same", activation="relu")(inp)  # short shapes
    x = layers.Conv1D(24, 7, strides=2, padding="same", activation="relu")(x)    # breath-long
    x = layers.Conv1D(32, 7, strides=2, padding="same", activation="relu")(x)    # several breaths
    x = layers.GlobalAveragePooling1D()(x)                                         # anywhere in 12 s
    x = layers.Dense(16, activation="relu")(x)
    out = layers.Dense(1, activation="sigmoid")(x)                                 # P(breathing)
    model = keras.Model(inp, out)
    model.compile(optimizer=keras.optimizers.Adam(1e-3), loss="binary_crossentropy",
                  metrics=["accuracy"])
    return model


def scores(p: np.ndarray, y: np.ndarray, what: np.ndarray) -> dict:
    """Accuracy overall and for each kind of slot, at the 0.5 threshold."""
    yes = p >= 0.5
    out = {"accuracy": round(float(np.mean(yes == (y == 1))), 4)}
    for name, w in (("baby", 0), ("parent", 1), ("far", 2)):
        m = what == w
        if m.any():
            out[f"{name}_accuracy"] = round(float(np.mean(yes[m] == (y[m] == 1))), 4)
    m = (what == 0) & (y == 0)
    out["baby_stopped_caught"] = round(float(np.mean(~yes[m])), 4) if m.any() else None
    m = (what == 0) & (y == 1)
    out["baby_breathing_seen"] = round(float(np.mean(yes[m])), 4) if m.any() else None
    return out


def to_int8_tflite(model, sample: np.ndarray) -> bytes:
    """8-bit model for the board: weights and activations int8, calibrated on real examples."""
    import tensorflow as tf
    conv = tf.lite.TFLiteConverter.from_keras_model(model)
    conv.optimizations = [tf.lite.Optimize.DEFAULT]
    conv.representative_dataset = lambda: ([x[None]] for x in sample)
    conv.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
    conv.inference_input_type = tf.int8
    conv.inference_output_type = tf.int8
    return conv.convert()


def run_int8(blob: bytes, X: np.ndarray) -> np.ndarray:
    """P(breathing) from the 8-bit model, to check it still agrees with the full model."""
    import tensorflow as tf
    it = tf.lite.Interpreter(model_content=blob)
    it.allocate_tensors()
    i, o = it.get_input_details()[0], it.get_output_details()[0]
    (si, zi), (so, zo) = i["quantization"], o["quantization"]
    out = np.empty(len(X), np.float32)
    for n, x in enumerate(X):
        q = np.clip(np.round(x / si + zi), -128, 127).astype(np.int8)
        it.set_tensor(i["index"], q[None])
        it.invoke()
        out[n] = (float(it.get_tensor(o["index"])[0, 0]) - zo) * so
    return out


def main():
    ap = argparse.ArgumentParser(description="Train the per-slot breathing model.")
    ap.add_argument("--epochs", type=int, default=15)
    ap.add_argument("--hop", type=int, default=16, help="lines between training windows (~1 s)")
    args = ap.parse_args()
    import tensorflow as tf
    tf.keras.utils.set_random_seed(5)
    rng = np.random.default_rng(5)

    X, y, what = examples(DATA / "train", args.hop, rng)
    Xt, yt, wt = examples(DATA / "test", args.hop, rng)
    print(f"train {len(y)} examples ({int(y.sum())} breathing), test {len(yt)} ({int(yt.sum())} breathing)")

    model = build_model()
    model.summary(print_fn=lambda s: None)
    weights = {0: 0.5 * len(y) / max(1, int((y == 0).sum())), 1: 0.5 * len(y) / max(1, int(y.sum()))}
    model.fit(X, y, epochs=args.epochs, batch_size=256, validation_split=0.1, class_weight=weights,
              verbose=2, callbacks=[tf.keras.callbacks.EarlyStopping(patience=3, restore_best_weights=True)])

    p = model.predict(Xt, batch_size=1024, verbose=0)[:, 0]
    MODELS.mkdir(parents=True, exist_ok=True)
    model.save(MODELS / "breath_slot.keras")
    blob = to_int8_tflite(model, X[rng.choice(len(X), size=500, replace=False)])
    (MODELS / "breath_slot_int8.tflite").write_bytes(blob)
    p8 = run_int8(blob, Xt)
    report = {
        "params": int(model.count_params()),
        "int8_bytes": len(blob),
        "test_full": scores(p, yt, wt),
        "test_int8": scores(p8, yt, wt),
        "int8_agrees_with_full": round(float(np.mean((p8 >= 0.5) == (p >= 0.5))), 4),
        "window_lines": STEPS * 2, "channels": CHANNELS,
    }
    (MODELS / "breath_slot.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
