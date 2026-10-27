"""Both models in plain numpy, so the app (Python 3.14, no TensorFlow) can run them.

After training, export the weights once with the AI environment:
    ../ml-env/bin/python -m ml.runtime --export
This writes ml/data/models/<name>.json (the layers) and <name>.npz (the weights) next to each
.keras file, and checks that numpy gives the same answers as TensorFlow.

Only the layer types our two models use are supported: Conv1D, Conv2D, MaxPooling2D,
GlobalAveragePooling1D, Flatten and Dense, with "same" padding as in Keras.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

MODELS = Path(__file__).resolve().parent / "data" / "models"
NAMES = ("breath_slot", "state")


def _act(x: np.ndarray, name: str) -> np.ndarray:
    if name == "relu":
        return np.maximum(x, 0.0)
    if name == "sigmoid":
        return 1.0 / (1.0 + np.exp(-x))
    if name == "softmax":
        e = np.exp(x - x.max(axis=-1, keepdims=True))
        return e / e.sum(axis=-1, keepdims=True)
    return x


def _same_pad(n: int, k: int, s: int) -> tuple[int, int, int]:
    """Keras "same" padding: output length, padding before and after."""
    out = -(-n // s)
    pad = max((out - 1) * s + k - n, 0)
    return out, pad // 2, pad - pad // 2


def _conv1d(x, w, b, stride):
    """x (N, T, C), w (K, C, F)."""
    out, lo, hi = _same_pad(x.shape[1], w.shape[0], stride)
    xp = np.pad(x, ((0, 0), (lo, hi), (0, 0)))
    idx = np.arange(out)[:, None] * stride + np.arange(w.shape[0])[None, :]
    return np.einsum("ntkc,kcf->ntf", xp[:, idx], w) + b


def _conv2d(x, w, b, strides):
    """x (N, H, W, C), w (KH, KW, C, F)."""
    (oh, th, bh), (ow, lw, rw) = (_same_pad(x.shape[1], w.shape[0], strides[0]),
                                  _same_pad(x.shape[2], w.shape[1], strides[1]))
    xp = np.pad(x, ((0, 0), (th, bh), (lw, rw), (0, 0)))
    ih = np.arange(oh)[:, None] * strides[0] + np.arange(w.shape[0])[None, :]
    iw = np.arange(ow)[:, None] * strides[1] + np.arange(w.shape[1])[None, :]
    patches = xp[:, ih[:, None, :, None], iw[None, :, None, :]]  # (N, oh, ow, KH, KW, C)
    return np.einsum("nabklc,klcf->nabf", patches, w) + b


class Model:
    """One exported model: run(x) takes a batch, like Keras' predict."""

    def __init__(self, name: str, folder: Path = MODELS):
        self.layers = json.loads((folder / f"{name}.json").read_text())
        w = np.load(folder / f"{name}.npz")
        self.weights = [[w[f"{i}_{j}"] for j in range(layer["n_weights"])]
                        for i, layer in enumerate(self.layers)]

    def run(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, np.float32)
        for layer, ws in zip(self.layers, self.weights):
            kind = layer["type"]
            if kind == "Conv1D":
                x = _act(_conv1d(x, ws[0], ws[1], layer["strides"][0]), layer["activation"])
            elif kind == "Conv2D":
                x = _act(_conv2d(x, ws[0], ws[1], layer["strides"]), layer["activation"])
            elif kind == "MaxPooling2D":
                ph, pw = layer["pool_size"]
                n, h, w_, c = x.shape
                x = x[:, :h - h % ph, :w_ - w_ % pw].reshape(n, h // ph, ph, w_ // pw, pw, c).max(axis=(2, 4))
            elif kind == "GlobalAveragePooling1D":
                x = x.mean(axis=1)
            elif kind == "Flatten":
                x = x.reshape(len(x), -1)
            elif kind == "Dense":
                x = _act(x @ ws[0] + ws[1], layer["activation"])
            else:
                raise ValueError(f"layer {kind} not supported")
        return x


def export(name: str) -> float:
    """Write <name>.json/.npz from <name>.keras; returns the largest difference to TensorFlow."""
    from tensorflow import keras
    from ml.state_model import partial_loss
    m = keras.models.load_model(MODELS / f"{name}.keras", custom_objects={"partial_loss": partial_loss})
    layers, arrays = [], {}
    for layer in m.layers:
        kind = type(layer).__name__
        if kind == "InputLayer":
            continue
        cfg = layer.get_config()
        ws = layer.get_weights()
        layers.append({"type": kind, "n_weights": len(ws), "activation": cfg.get("activation", "linear"),
                       "strides": list(cfg.get("strides", [1])), "pool_size": list(cfg.get("pool_size", [1, 1])),
                       "padding": cfg.get("padding", "valid")})
        for j, a in enumerate(ws):
            arrays[f"{len(layers) - 1}_{j}"] = a.astype(np.float32)
    (MODELS / f"{name}.json").write_text(json.dumps(layers, indent=1))
    np.savez(MODELS / f"{name}.npz", **arrays)
    x = np.random.default_rng(1).random((64,) + tuple(m.input_shape[1:]), dtype=np.float32)
    return float(np.abs(Model(name).run(x) - m.predict(x, verbose=0)).max())


def main():
    ap = argparse.ArgumentParser(description="Export the models for numpy and check them.")
    ap.add_argument("--export", action="store_true")
    if ap.parse_args().export:
        for name in NAMES:
            print(name, "largest difference numpy vs TensorFlow:", export(name))


if __name__ == "__main__":
    main()
