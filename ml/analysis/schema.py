"""Version 1 canonical radar dataset schema; Arrow is an offline-only dependency."""
from __future__ import annotations

from dataclasses import asdict, dataclass

SCHEMA_VERSION = 1
FORMAT_NAME = "ibuki.radar.capture"
SOURCE_TYPES = ("repository_sample", "board_capture", "synthetic", "unknown")
DATASET_FILES = ("manifest.json", "frames.parquet", "events.jsonl", "quality.json")
INT64_MIN, INT64_MAX = -(2**63), 2**63 - 1


def require_arrow():
    """Keep importing other analysis/live modules independent of PyArrow."""
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except (ImportError, OSError) as exc:
        raise ImportError("Canonical ingestion requires offline dependency pyarrow. Install with: "
                          "python -m pip install -r ml/analysis/requirements.txt") from exc
    return pa, pq


@dataclass(frozen=True)
class FrameRecord:
    session_id: str
    source_line: int
    message_type: str
    seq: int
    board_uptime_ms: int
    antenna_count: int
    bin_count: int
    raw_i: tuple[tuple[int, ...], ...]
    raw_q: tuple[tuple[int, ...], ...]
    wire_encoding: str
    value_divisor: float | None
    scaled_values_available: bool
    configuration_status: str
    config_source_line: int | None
    first_cm: float | None
    step_mm: float | None
    config_bin_count: int | None
    config_scale: float | None
    schema_version: int = SCHEMA_VERSION

    def to_row(self) -> dict:
        return asdict(self)


def frame_schema():
    """Nested I/Q lists are indexed [antenna][bin], with exact signed int64 storage.

    D has one antenna and decimal wire integers; value = raw / observed C.scale.
    E has three antennas, 24 bins, int16 LE wire values already in value units.
    Missing C never supplies geometry; E still has value_divisor=1 independently.
    """
    pa, _ = require_arrow()
    integer_matrix = pa.list_(pa.list_(pa.int64()))
    return pa.schema([
        pa.field("schema_version", pa.int16(), nullable=False),
        pa.field("session_id", pa.string(), nullable=False),
        pa.field("source_line", pa.int64(), nullable=False),
        pa.field("message_type", pa.string(), nullable=False),
        pa.field("seq", pa.int64(), nullable=False),
        pa.field("board_uptime_ms", pa.int64(), nullable=False),
        pa.field("antenna_count", pa.int8(), nullable=False),
        pa.field("bin_count", pa.int32(), nullable=False),
        pa.field("raw_i", integer_matrix, nullable=False),
        pa.field("raw_q", integer_matrix, nullable=False),
        pa.field("wire_encoding", pa.string(), nullable=False),
        pa.field("value_divisor", pa.float64()),
        pa.field("scaled_values_available", pa.bool_(), nullable=False),
        pa.field("configuration_status", pa.string(), nullable=False),
        pa.field("config_source_line", pa.int64()),
        pa.field("first_cm", pa.float64()),
        pa.field("step_mm", pa.float64()),
        pa.field("config_bin_count", pa.int64()),
        pa.field("config_scale", pa.float64()),
    ], metadata={b"ibuki.schema_version": b"1", b"ibuki.time": b"board uptime milliseconds, not Unix time",
                 b"ibuki.raw_layout": b"nested [antenna][bin]; separate I and Q integer arrays"})
