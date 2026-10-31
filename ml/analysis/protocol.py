"""D/C capture schema. t_ms is milliseconds since board boot, never Unix time."""
from __future__ import annotations

from dataclasses import dataclass


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
