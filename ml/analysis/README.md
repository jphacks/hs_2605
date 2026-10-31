# Offline radar captures

Run from the repository root with Python 3.10 or newer. The parser uses only
the standard library; validation against existing ML readers uses the NumPy
already included in the PC requirements. No TensorFlow, training, GPU or board
is needed.

```python
from ml.analysis.protocol import read_capture

capture = read_capture("recordings/samples/breathing.sample.csv")
print(len(capture.frames), len(capture.configs), len(capture.issues))
# 999 12 0
frame = capture.frames[0]
print(frame.data.seq, frame.data.t_ms, frame.config_line_number)
# 12875 774777 6
print(frame.data.i[0], frame.data.q[0], frame.z()[0])
# -4874 -5908 (-487.4-590.8j)
if frame.config is not None:
    distances_cm = frame.config.ranges_cm()
for issue in capture.issues:
    print(issue.line_number, issue.reason, issue.raw)
```

`parse_line(text)` returns `Data`, `Config`, or an uninterpreted `OtherMessage`.
Malformed D/C and blank input raise `ValueError`. `parse_capture(lines)` accepts
an iterable of raw rows; `read_capture(path)` reads a recorder CSV. Capture errors
are collected with 1-based physical line numbers, the original row, and a reason.
Malformed rows are skipped; subsequent rows are still read. An empty file returns
an empty capture. Filesystem errors propagate. UTF-8 BOM is accepted and invalid
UTF-8 bytes are replaced, causing invalid numeric fields to be reported.

`Data.i` and `Data.q` are immutable tuples of Python integers in nearest-first
bin order. They preserve original integer values without a floating point cast.
`seq` is the board sequence number. **`t_ms` is elapsed milliseconds since board
boot, not a Unix timestamp or a host recording time.** Values and ordering are
preserved across gaps/restarts; this package does not repair or resample them.

`Config` contains `first_cm`, `step_mm`, `n`, and `scale`. The bin distance is
`first_cm + bin_index * step_mm / 10`; the complex value is
`I / scale + 1j * Q / scale`. Floating point conversion occurs only in `z()`.

The recorder saves raw incoming rows and can start between periodic C messages.
Consequently, the first valid C is also associated with preceding D rows; each
frame exposes its `config_line_number`, which may be later than its own line.
This assumes the first observed configuration applied to that initial interval;
the capture cannot prove historical configuration before recording began.
Subsequent valid C messages apply only to subsequent D rows. Malformed C rows are
reported and do not replace the last valid C. No C means `config` and
`config_line_number` remain `None`, and `frame.z()` raises `ValueError`.
No range or scale defaults are inferred from ML constants.

A D/C bin-count mismatch is reported while retaining the D frame's raw integers;
`z()` refuses conversion. Inspect `capture.issues` before downstream analysis.
The reader loads the capture in memory to associate late configuration.

## Relationship to existing readers

`pc/ibuki/serial_reader.py` remains the live transport/parser: it silently ignores
bad lines and stores raw bins as complex floating point arrays. `ml/features.py`
is the model input reader with fixed 24-bin/scale-10 assumptions and E support;
`ml/antennas.py` decodes E for antenna analysis. Those runtime paths are unchanged.
The separate offline parser provides lossless integers and row diagnostics without
importing the PC application or adopting model-specific defaults. Tests compare
all D rows in all three public samples exactly with `ml.features.read_csv` and
check conversion against the live parser.

E/Y/H/S and other nonempty message kinds are retained in `other_messages` with
source line numbers, without payload validation or interpretation. Quality
diagnostics below count these message kinds without decoding them.

## Capture quality diagnostics

Use the standard-library-only diagnostics to check recording integrity without
hardware. The API consumes the existing parser result and never repairs samples:

```python
from ml.analysis.protocol import read_capture
from ml.analysis.quality import analyze_quality

capture = read_capture("recordings/samples/breathing.sample.csv")
report = analyze_quality(capture)
print(report.to_dict())
```

From the repository root:

```sh
python -m ml.analysis.quality recordings/samples/breathing.sample.csv
python -m ml.analysis.quality recordings/samples/breathing.sample.csv --json
```

The default summary prints descriptive metric labels; JSON uses the stable keys
below, sorted deterministically, with unavailable values as `null`. Reports go
to stdout only; no files are written. Empty files succeed with zero counts and
unavailable timing metrics. Files without D (including E-only captures) also have
unavailable timing metrics. Missing/unreadable files produce an error on stderr,
no report on stdout, and exit status 2. Row issues remain in the capture and do
not cause a nonzero CLI status; consumers should inspect the issue counts.

Measured breathing sample JSON:

```json
{
  "backward_sequence_events": 0,
  "backward_timestamp_events": 0,
  "bin_count_mismatch_frames": 0,
  "duplicate_sequence_events": 0,
  "estimated_missing_frames": 0,
  "frames_without_configuration": 0,
  "frames_without_usable_configuration": 0,
  "issue_source_line_count": 0,
  "long_interval_events": 0,
  "long_interval_multiplier": 3.0,
  "long_interval_threshold_ms": 180.0,
  "parser_issue_count": 0,
  "repeated_timestamp_events": 0,
  "sequence_gap_events": 0,
  "suspected_reset_events": 0,
  "timing_reference_interval_count": 998,
  "total_d_frames": 999,
  "typical_sampling_interval_ms": 60.0,
  "unparsed_message_kind_counts": {}
}
```

All three public samples were measured, rather than assumed continuous:

| Sample | D frames | Estimated missing | Seq gaps/duplicates/backward | Repeated/backward time | Typical interval | Long intervals |
| --- | ---: | ---: | --- | --- | --- | ---: |
| breathing | 999 | 0 | 0 / 0 / 0 | 0 / 0 | 60 ms | 0 |
| hold | 1000 | 0 | 0 / 0 / 0 | 0 / 0 | 60 ms | 0 |
| leave | 996 | 0 | 0 / 0 / 0 | 0 / 0 | 60 ms | 0 |

Each has zero parser issues and zero frames without usable configuration. Their
timing reference counts are 998, 999, and 995 respectively; each long-interval
threshold is 180 ms. Their unparsed-message dictionaries are empty.

### Metric definitions and JSON fields

All adjacency metrics compare consecutive **parsed D frames in file order**,
even if C, other messages, or malformed rows occur between them. Let `ds` and
`dt` be current minus previous seq and t_ms. The first D has no predecessor.

| JSON key | Definition |
| --- | --- |
| `total_d_frames` | All parsed D frames, including missing/mismatched configuration. |
| `parser_issue_count` | Number of existing CaptureIssue entries; no re-parsing. |
| `issue_source_line_count` | Distinct physical source line numbers across those entries. |
| `estimated_missing_frames` | Sum of `ds - 1` where `ds > 1` and `dt >= 0`; an estimate, not reconstructed data. |
| `sequence_gap_events` | Number of those positive sequence-gap pairs. |
| `duplicate_sequence_events` | Pairs with `ds == 0`, regardless of timestamp or payload. Not a global repeated-ID count. |
| `backward_sequence_events` | Pairs with `ds < 0`; never interpreted as huge positive gaps. |
| `repeated_timestamp_events` | Pairs with `dt == 0`, regardless of seq. |
| `backward_timestamp_events` | Pairs with `dt < 0`, regardless of seq. |
| `suspected_reset_events` | Pairs with `ds < 0` OR `dt < 0`, counted once if both; not proof of a reboot. |
| `timing_reference_interval_count` | Positive `dt` intervals with `ds == 1`. |
| `typical_sampling_interval_ms` | Median of reference intervals if there are at least three; otherwise null. |
| `long_interval_multiplier` | Fixed deterministic policy, 3.0. |
| `long_interval_threshold_ms` | Typical interval times multiplier, otherwise null. |
| `long_interval_events` | Forward seq/time pairs (`ds > 0`, `dt > 0`) whose raw dt strictly exceeds the threshold; null without a threshold. Sequence gaps may also be long intervals. |
| `frames_without_configuration` | Frames with config None; no model defaults inferred. |
| `bin_count_mismatch_frames` | Frames with config but I or Q count unequal to config.n. |
| `frames_without_usable_configuration` | Union of missing config, invalid range/scale config, and bin mismatch. Valid config requires finite first_cm/step_mm/scale, first_cm >= 0, and step_mm/n/scale > 0. |
| `unparsed_message_kind_counts` | Sorted mapping of OtherMessage kind to row count, including E/Y/H/S when present. These payloads are not validated. |

These fields may overlap: a mismatched frame is both unusable and a parser issue,
and a positive sequence gap may also be a long time gap. Do not sum these counts
into a composite score. The parser already rejects invalid C messages; usability
checks also protect captures assembled through the Python structures directly.

### Interpretation and limitations

**Parsing success** means a row fits the supported protocol. **Capture
completeness** concerns sequence/time continuity of the observed D frames.
**Signal quality** concerns the radar measurements themselves; this tool does
not measure noise, motion, SNR, or physiological validity. **AI inference
accuracy** requires labeled evaluation and cannot be inferred from this report.
Zero integrity anomalies therefore do not establish signal quality or accuracy.

Missing-frame estimates cannot detect loss before the first or after the last
observed D. Malformed D rows may contribute both parser issues and inferred seq
loss. Backward seq/time can reflect restarts, wraparound, or out-of-order rows;
they are boundaries, and positive seq gaps across backward time are excluded.
No wraparound or restart is automatically corrected. No duration or sampling
rate is calculated across suspected resets. `t_ms` remains board uptime.

The median policy requires three reference intervals but cannot prove stable
cadence: predominantly slow intervals or mixed recording modes can bias it.
Duplicate/backward pairs are excluded from timing, and seq-gap intervals never
establish the baseline. A long interval can reflect missing rows or delayed
output, not a diagnosed hardware fault. C association follows the parser's
documented first-C backfill assumption. E-only captures have zero analyzed D,
not a claim that no radar data was recorded.

Quality diagnostics do not filter, interpolate, repair, or evaluate signals or
models. Offline inspection plots are available separately below.

## Offline capture visualization

Generate a headless PNG overview with NumPy and Matplotlib (already in the PC
requirements). No hardware, TensorFlow, model inference, or GUI is involved.

```sh
python -m ml.analysis.visualize recordings/samples/breathing.sample.csv --output breathing_overview.png --bin 10
python -m ml.analysis.visualize recordings/samples/hold.sample.csv --output hold_overview.png --bin 10
python -m ml.analysis.visualize recordings/samples/leave.sample.csv --output leave_overview.png --bin 10
```

The output is a 1950 x 1500 PNG with three panels:

1. **Discrete time/distance heatmap.** Each observed complex value `z` comes
   from `CaptureFrame.z()`. For each bin, subtract its unweighted temporal mean
   over the parsed D frames, then color by `abs(z - mean(z))` in scaled radar
   units. The magma color bar maps darker to smaller and brighter to larger
   variation. It uses a separate linear color range per report, not a shared
   scale across files. Subtracting the mean defines this display metric; it is
   not a signal filtering or noise-removal pipeline.
2. **Selected-bin I/Q.** Blue is the real component (I), orange the imaginary
   component (Q), both scaled by C.scale without centering or filtering. Bin
   selection is zero-based, default 0; valid indices are 0 through C.n - 1.
   Distance is obtained from `Config.ranges_cm()` and included in the title.
   The public samples have `C,15.0,37.5,24,10`, so bin 10 is 52.5 cm. Neither
   that bin count nor its geometry/scale is built into the implementation.
3. **Entire-recording integrity summary.** Values come directly from
   `analyze_quality(capture)`, including total parsed D, parser issues, estimated
   missing frames, seq gaps/duplicates/backward movement, suspected resets,
   repeated/backward times, long intervals/threshold, and unusable configuration.
   The plotted frame count and number of I/Q segments are displayed separately.
   Malformed rows are excluded from the plots but retained in the whole-file
   parser issue count. No composite score is calculated.

Both plots use `(t_ms - first_plotted_t_ms) / 1000`, subtracting integer uptime
before converting to seconds. Labels state the first D's board uptime and that
it is not Unix time. There is no uniform-time assumption. Heatmap columns are
vertical strokes at actual timestamps, with a fixed display width (0.8 points),
not rectangles filling estimated time intervals. Distance strokes span half a
configured bin step on either side of the bin center. Blank time intervals are
not filled with invented data; finite image resolution can overlap dense
columns. The heatmap is deliberately discrete rather than interpolated.

I/Q markers show every observed D. Connecting lines are broken at sequence gaps,
at parser issue lines between D frames, and at timestamp intervals exceeding
the quality report's long-interval threshold. If that threshold is unavailable,
line drawing conservatively uses three times the smallest observed positive
interval; this display policy does not modify the quality report. Line segments
are visual guides, not reconstructed intermediate measurements. A single D is
supported as one column/marker with unavailable timing metrics.

### Python API and file handling

```python
from ml.analysis.protocol import read_capture
from ml.analysis.visualize import prepare_visualization, render_report

capture = read_capture("recordings/samples/breathing.sample.csv")
data = prepare_visualization(capture, bin_index=10)
print(data.distances_cm[10], data.z.shape)
# 52.5 (999, 24)
render_report(capture, "breathing_overview.png", bin_index=10)
```

`prepare_visualization` returns actual elapsed coordinates, physical distances,
scaled complex values, variation values, contiguous trace slices, and the
whole-capture quality report. `build_figure(data)` returns a Figure using an Agg
canvas; `render_report` renders entirely in memory before opening its PNG output.
No pyplot global figures or GUI windows are created. Existing files are protected
by exclusive creation unless `--force` (Python: `force=True`) is supplied.
Validation failures leave an existing output unchanged, even with force.
Only `.png` is accepted; output parent directories must already exist. CLI
success prints the output path. Input, validation, or output errors print to
stderr and exit with status 2. No output is created on failed validation.

### Supported captures and limits

One consistent D configuration is supported. Repeated identical C messages and
the parser's documented late first-C association are valid. The tool rejects:

- Empty/no-D/E-only captures (three-antenna E payloads are not visualized).
- Missing/unusable configuration or I/Q bin-count mismatches.
- Any observed C geometry or scale change, including a trailing changed C.
- Duplicate/backward seq, suspected restarts, and repeated/backward timestamps.
- Out-of-range/noninteger bin indices and nonfinite numeric preparations.

Malformed rows may coexist with usable D frames; their count and plot exclusions
are explicit. E/Y/H/S payloads are not decoded, and their presence does not imply
that those messages were analyzed. The report covers only parsed D values.
The complete capture, complex matrix, and plot are kept in memory; large files
can need substantial memory. There is no downsampling or automatic repair.

Actual review examples were generated for all three public samples with bin 10:
999/1000/996 plotted frames, distances 15 to 101.25 cm (bin centers), and zero
reported parser/continuity/configuration anomalies. The measured 60 ms typical
interval gives a 180 ms long-gap threshold in each summary. In the leave sample,
the plots show a change near 23 seconds; this is an observed signal change, not
an inferred event label. Generated binaries are kept outside tracked source.

These plots support later signal analysis by exposing distance regions and time
intervals worth investigating alongside recording integrity. They do not measure
breathing, respiratory rate, chest displacement, medical status, or AI accuracy.
No filtering, automatic noise removal, classification, live dashboard, 3D,
three-antenna direction plotting, Parquet, or HTML report is implemented.

## Validation

```sh
python -m unittest discover -s ml/analysis/tests -t . -v
python -m unittest discover -s pc/tests -t pc -v
```
