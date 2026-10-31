"""D/C capture schema. t_ms is milliseconds since board boot, never Unix time."""
from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import re
from typing import Iterable


@dataclass(frozen=True)
class Config:
    first_cm: float
    step_mm: float
    n: int
    scale: float

    def ranges_cm(self) -> tuple[float, ...]:
        return tuple(self.first_cm + k * self.step_mm / 10 for k in range(self.n))


@dataclass(frozen=True)
class Data:
    seq: int
    t_ms: int
    i: tuple[int, ...]
    q: tuple[int, ...]

    def z(self, config: Config) -> tuple[complex, ...]:
        """Convert raw integers to value units using an explicitly supplied C."""
        if len(self.i) != config.n or len(self.q) != config.n:
            raise ValueError(f"D has {len(self.i)} I/{len(self.q)} Q bins; C expects {config.n}")
        return tuple(complex(i / config.scale, q / config.scale)
                     for i, q in zip(self.i, self.q))


@dataclass(frozen=True)
class OtherMessage:
    """Uninterpreted message retained for future E/Y/H/S support."""
    kind: str
    raw: str


Message = Config | Data | OtherMessage


@dataclass(frozen=True)
class AntennaData:
    """E wire integers already in value units, indexed [antenna][nearest-first bin]."""
    seq: int
    t_ms: int
    i: tuple[tuple[int, ...], ...]
    q: tuple[tuple[int, ...], ...]


def parse_e_line(line: str) -> AntennaData:
    """Strict optional E decoder; existing parse_line/parse_capture behavior is unchanged.

    Exactly 3 antennas x 24 bins x I/Q, signed int16 little-endian, no C.scale.
    """
    import base64
    import struct

    p = [part.strip() for part in line.strip().split(",")]
    if len(p) != 4 or p[0] != "E":
        raise ValueError("E requires seq, t_ms and one base64 payload")
    seq, t_ms = _integer(p[1], "seq"), _integer(p[2], "t_ms")
    if seq < 0 or t_ms < 0:
        raise ValueError("seq and t_ms must be nonnegative")
    try:
        payload = base64.b64decode(p[3], validate=True)
    except ValueError as exc:
        raise ValueError("E payload is not valid base64") from exc
    if len(payload) != 3 * 24 * 2 * 2:
        raise ValueError("E payload must contain 288 bytes (3 antennas x 24 bins x int16 I/Q)")
    values = struct.unpack("<144h", payload)
    i = tuple(tuple(values[a*48 + k*2] for k in range(24)) for a in range(3))
    q = tuple(tuple(values[a*48 + k*2 + 1] for k in range(24)) for a in range(3))
    return AntennaData(seq, t_ms, i, q)


@dataclass(frozen=True)
class CaptureIssue:
    line_number: int
    reason: str
    raw: str


@dataclass(frozen=True)
class CaptureFrame:
    line_number: int
    data: Data
    config: Config | None
    config_line_number: int | None

    def z(self) -> tuple[complex, ...]:
        if self.config is None:
            raise ValueError("No C message: scale and range configuration are unknown")
        return self.data.z(self.config)


@dataclass(frozen=True)
class Capture:
    frames: tuple[CaptureFrame, ...]
    configs: tuple[tuple[int, Config], ...]
    other_messages: tuple[tuple[int, OtherMessage], ...]
    issues: tuple[CaptureIssue, ...]


def _integer(value: str, field: str) -> int:
    if re.fullmatch(r"[+-]?[0-9]+", value) is None:
        raise ValueError(f"{field}: expected an integer, got {value!r}")
    return int(value)


def parse_line(line: str) -> Message:
    """Parse D/C strictly; preserve other nonempty messages without decoding them.

    Raises ValueError on malformed D/C or blank input. No model defaults are used.
    """
    raw = line.strip()
    p = [part.strip() for part in raw.split(",")]
    if not p[0]:
        raise ValueError("empty message")
    if p[0] == "D":
        if len(p) < 5 or (len(p) - 3) % 2:
            raise ValueError("D requires seq, t_ms and complete I/Q pairs")
        seq, t_ms = _integer(p[1], "seq"), _integer(p[2], "t_ms")
        if seq < 0 or t_ms < 0:
            raise ValueError("seq and t_ms must be nonnegative")
        values = tuple(_integer(v, f"I/Q[{k}]") for k, v in enumerate(p[3:]))
        return Data(seq, t_ms, values[::2], values[1::2])
    if p[0] == "C":
        if len(p) != 5:
            raise ValueError("C requires first_cm, step_mm, n, scale")
        try:
            first, step, scale = float(p[1]), float(p[2]), float(p[4])
        except ValueError as exc:
            raise ValueError("C first_cm, step_mm and scale must be numbers") from exc
        n = _integer(p[3], "n")
        if not all(math.isfinite(v) for v in (first, step, scale)):
            raise ValueError("C values must be finite")
        if first < 0 or step <= 0 or n <= 0 or scale <= 0:
            raise ValueError("C requires first_cm >= 0 and step_mm, n, scale > 0")
        return Config(first, step, n, scale)
    return OtherMessage(p[0], raw)


def parse_capture(lines: Iterable[str]) -> Capture:
    """Read all rows and collect errors without aborting.

    The first valid C is associated with preceding D rows (periodic boot config).
    Later C rows apply forward only. The source line makes this assumption visible.
    Bin-mismatched D rows are retained as raw frames and reported as issues;
    their z() conversion raises ValueError. Blank rows are reported too.
    """
    data, configs, other, issues = [], [], [], []
    for number, line in enumerate(lines, 1):
        try:
            message = parse_line(line)
        except ValueError as exc:
            issues.append(CaptureIssue(number, str(exc), line.rstrip("\r\n")))
            continue
        if isinstance(message, Config):
            configs.append((number, message))
        elif isinstance(message, Data):
            data.append((number, message, configs[-1] if configs else None,
                         line.rstrip("\r\n")))
        else:
            other.append((number, message))
    frames = []
    for number, message, source, raw in data:
        source = source or (configs[0] if configs else None)
        config_line, config = source if source else (None, None)
        frames.append(CaptureFrame(number, message, config, config_line))
        if config is not None and len(message.i) != config.n:
            issues.append(CaptureIssue(number,
                                      f"D has {len(message.i)} bins; C expects {config.n}",
                                      raw))
    return Capture(tuple(frames), tuple(configs), tuple(other),
                   tuple(sorted(issues, key=lambda issue: issue.line_number)))


def read_capture(path: str | Path) -> Capture:
    """Load a raw pc/record.py file; filesystem errors propagate to the caller."""
    with Path(path).open(encoding="utf-8-sig", errors="replace") as stream:
        return parse_capture(stream)
