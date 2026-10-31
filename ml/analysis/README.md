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

## Temporal signal diagnostics

`ml.analysis.signal` explores variation and spectral structure before choosing
filters or engineering new features. It reuses the parser and whole-file
`analyze_quality` report and requires only the existing NumPy/SciPy packages.

```sh
python -m ml.analysis.signal recordings/samples/breathing.sample.csv --bin 9 --window-sec 12 --hop-sec 3 --json
python -m ml.analysis.signal recordings/samples/hold.sample.csv --bin 9 --window-sec 12 --hop-sec 3 --json
python -m ml.analysis.signal recordings/samples/leave.sample.csv --bin 9 --window-sec 12 --hop-sec 3 --json
```

Without `--json`, the CLI prints a compact per-window summary. JSON is sorted,
deterministic for the same input/environment, and contains no generated wall-clock
time. Output goes only to stdout, never to automatically created files. Missing
input, invalid bin/window/hop, and unsupported captures produce stderr and exit
status 2. Empty/E-only files, absent/mismatched/changing C, nonfinite values,
duplicate/backward seq, repeated/backward time and suspected restarts are rejected.
Positive sequence gaps and irregular positive timestamps remain analyzable, with
explicitly unavailable spectral results in affected windows.

```python
from ml.analysis.protocol import read_capture
from ml.analysis.signal import analyze_signal

report = analyze_signal(read_capture("recordings/samples/breathing.sample.csv"),
                        bin_index=9, window_sec=12, hop_sec=3)
results = report.to_dict()
```

### Windows and signal definition

Window starts are anchored to the first parsed D's **board uptime**, every hop
through the last observed D: `[start_t_ms, start_t_ms + window_ms)`. Right-boundary
samples belong to the next window, not the previous one. Positive window/hop
lengths must resolve to whole milliseconds. Overlap is permitted; overlapping
windows are not independent observations. No resampling or samples are invented.
Windows falling in a gap can contain zero samples. Tail windows are retained:
`complete_window` means the requested end is at or before the capture's last
observed timestamp, not a promise that all expected samples exist. Partial
windows can have RMS but never spectral peaks. The last start may coincide with
the last D, producing a one-sample window. Requested start/end and observed
first/last timestamps are separate fields; all are uptime, not Unix time.

The 12-second default relates to `ml.features.WINDOW = 200`, approximately 12 s
at the board's nominal cadence. The existing model uses a fixed **sample count**;
these diagnostics use actual elapsed time. They are not interchangeable after
loss or variable cadence. No model constants are used as a sampling rate.

For the selected bin, scaled complex values are `CaptureFrame.z()[bin_index]`.
With `c = z - mean(z)`, define:

- `rms_variation = sqrt(mean(abs(c)**2))`, in scaled radar units.
- Project onto `axis = angle(sum(c**2))/2`, using
  `wave = real(c * exp(-1j*axis))`, then
  `projected_rms = sqrt(mean(wave**2))` in the same units.

This is the same single-bin projection as `ml.features.window_features`, verified
against its actual model feature output. Here it is used without neighbor
normalization, paired-sample decimation, or logarithmic transforms. Its sign is
arbitrary and is not calibrated displacement. Constant inputs explicitly produce
zero variation/projection. Fewer than two samples gives null RMS. Means and RMS
are unweighted over observed samples, not time-weighted estimates; gaps and
irregular timing therefore change which observations contribute.

### Conservative spectral policy

The projected real signal is examined only when all these conditions hold:

- At least eight observed samples and a complete requested window.
- Consecutive selected seq values (delta exactly one).
- No parser issue source line between the first and last selected D.
- Positive, approximately uniform observed time spacing.
- First/last selected samples within 1.5 measured intervals of the requested
  boundaries, to avoid analyzing an apparently full window with uncovered edges.

The measured interval is `(last_t_ms - first_t_ms) / (N-1)`. Both the largest
interval deviation from this mean **and** the largest deviation from the implied
uniform grid must be at most `max(1 ms, 0.02 * measured_interval_ms)`. This policy
admits small millisecond quantization jitter but rejects larger irregularity and
accumulated timing drift. Thresholds, measured interval, and deviation magnitudes
are exposed. Timestamp validation happens before spectral calculation. Admitted
jitter is an approximation, not proof of exact uniform sampling.

For eligible nonconstant windows, SciPy's Hann `periodogram` uses
`fs = 1000 / measured_interval_ms`, no additional detrending, no zero padding,
and spectrum scaling. The projected wave is amplitude-normalized only for numeric
stability; this does not change peak location or power ratios. DC is excluded.

- `dominant_frequency_hz` is the largest remaining periodogram bin, over the
  entire positive range through Nyquist; there is no respiratory band assumption.
- `spectral_resolution_hz = fs / N`; the reported peak lies on this discrete grid.
- `spectral_concentration` is power in the peak bin and its immediate available
  neighbors (radius one), divided by all positive-frequency power. It is in
  [0, 1], is affected by Hann leakage and window length, and is not a calibrated
  probability, significance test, or composite quality score.

No filtering, interpolation, denoising, or peak-to-respiratory-rate conversion
is performed. Even very weak nonconstant input can have a dominant bin. Trends
can dominate the lowest nonzero bin. A short record or broad peak does not
establish periodicity merely because a maximum exists.

### JSON fields and unavailable values

Top-level fields are `bin_index`, `distance_cm`, effective `window_sec` and
`hop_sec`, `spectral_policy` (minimum sample count, jitter tolerances, Hann/DC/
concentration policy), existing whole-capture `quality`, and ordered `windows`.
Each window contains:

| Fields | Meaning |
| --- | --- |
| `window_start_t_ms`, `window_end_t_ms` | Requested half-open interval in board uptime milliseconds. |
| `observed_start_t_ms`, `observed_end_t_ms` | First/last included D uptime; null if empty. |
| `observed_samples`, `complete_window` | Observed count and temporal-boundary coverage as defined above. |
| `rms_variation`, `projected_rms` | Complex-centered and projected RMS; null with fewer than two samples. |
| `sampling_interval_ms` | Mean observed spacing; measurable even when FFT is rejected, so not necessarily the underlying cadence. |
| `interval_tolerance_ms` | Effective spacing/grid tolerance. |
| `max_interval_deviation_ms`, `max_grid_deviation_ms` | Measured timing deviations, null with fewer than two samples. |
| `spectral_status` | Explicit eligibility/result reason below. |
| `spectral_resolution_hz`, `dominant_frequency_hz`, `spectral_concentration` | Available spectral outputs; otherwise null. |

Status checks run in order: `insufficient_samples`, `partial_window`,
`sequence_gap`, `parser_issues`, `irregular_timing`, `incomplete_coverage`, then
`constant_signal` or `available`. A window can have multiple problems; its status
names the first applicable reason. Existing quality metrics still describe the
entire file, not just a selected window. Captures are loaded in memory and windows
are emitted at every hop, including gaps; use sensible hop sizes for long files.

### Measured public-sample comparison

The following actual measurements use **bin 9 (48.75 cm)**, 12-second windows,
and 3-second hop. RMS is complex-centered, in scaled radar units. Times in this
table are seconds from the first D, not asserted breath-hold/leave event labels.

| Sample | Median RMS over complete windows | 0-12 s RMS | 24-36 s RMS | 45-57 s RMS |
| --- | ---: | ---: | ---: | ---: |
| breathing | 371.7680 | 287.9260 | 375.2042 | 395.4441 |
| hold | 15.0202 | 19.8027 | 9.6672 | 13.7083 |
| leave | 4.6580 | 9.0779 | 0.9515 | 0.9862 |

For those same windows (peak Hz / spectral concentration):

| Sample | 0-12 s | 24-36 s | 45-57 s |
| --- | --- | --- | --- |
| breathing | 0.0832 / 0.2005 | 0.4992 / 0.3125 | 0.6656 / 0.5927 |
| hold | 0.0832 / 0.3953 | 0.2496 / 0.6174 | 0.3328 / 0.2548 |
| leave | 0.1664 / 0.5054 | 3.9936 / 0.0717 | 6.3233 / 0.0858 |

For example, breathing's first requested window is `[774777, 786777)` ms,
contains 200 samples through 786735 ms, has projected RMS 214.5069, measured
interval 60.09045 ms, and frequency resolution about 0.08321 Hz. There are 16/17/16
spectrally eligible complete windows in breathing/hold/leave. Breathing and leave
each have four tail partial windows. Hold has three partial windows and a final
one-sample window (insufficient samples), because its last D is just beyond 60 s.

The hold file still varies; its name supplies no exact hold interval. The selected
distance bin is also a confounder: different recordings can have their strongest
reflector at different ranges. These measurements do not demonstrate that the
features classify breathing or apnea. In leave, later selected windows have lower
variation and more diffuse spectral power despite a numerical peak at higher
frequencies; a peak alone must not be treated as a physiological rate.

These findings can guide later work: compare multiple bins and labeled intervals,
inspect low-frequency drift before selecting detrending/filter policies, evaluate
short-window frequency resolution and peak concentration, and measure how timing
loss changes feature availability. Any new filter/feature must be validated on
labeled recordings, rather than chosen from filenames or these three examples
alone. This PR changes no models, live detection, alarms, firmware, or dashboard.

## Offline denoising benchmark

`ml.analysis.denoise` compares unchanged scaled complex I/Q with **causal**
Butterworth low-pass and band-pass outputs. This is an offline experiment, not
a recommended filter, classifier, or change to runtime/model inputs.

```sh
python -m ml.analysis.denoise --synthetic --sample-hz 20 --seed 7 --output synthetic20.png --json > synthetic20.json
python -m ml.analysis.denoise --synthetic --sample-hz 10 --seed 7 --output synthetic10.png --json > synthetic10.json
python -m ml.analysis.denoise recordings/samples/breathing.sample.csv --bin 9 --output breathing_filters.png --json > breathing_filters.json
python -m ml.analysis.denoise recordings/samples/hold.sample.csv --bin 9 --output hold_filters.png --json > hold_filters.json
python -m ml.analysis.denoise recordings/samples/leave.sample.csv --bin 9 --output leave_filters.png --json > leave_filters.json
```

`--output` is optional; without it no files are written. JSON goes to stdout;
PNG success messages go to stderr with `--json`, preserving machine readability.
Omitting `--json` prints descriptive metrics. Existing PNG paths require
`--force`, supported output is `.png`, and parent directories must exist.
PNG encoding happens in memory before exclusive output creation. Validation
failures create no report and leave an existing report unchanged, even with
force. Invalid arguments/input produce stderr and exit status 2.

Configurable flags are `--order` (default 2, integer 1-12), `--lowpass-hz`
(1.5), `--band-low-hz` (0.1), and `--band-high-hz` (1.5). Cutoffs must be finite,
positive, correctly ordered, and strictly below measured Nyquist. These are
**exploratory settings, not validated physiological thresholds**. The band-pass
transformation has twice the transfer-function order specified by `--order`:
default low-pass is order 2, default band-pass order 4. SciPy represents both
using second-order sections (SOS).

### Data domain, cadence, and state

The selected bin uses `CaptureFrame.z()` and `Config.ranges_cm()` with no assumed
scale/geometry. Default bin is 9 (48.75 cm in the public samples). The recording
validator reuses visualization preparation and whole-capture quality diagnostics.
It rejects empty/E-only captures, missing/mismatched/changing C, repeated/backward
seq or time, **any parser issue**, seq gaps, and insufficient/irregular timing.
Repeated identical C and the parser's documented late first-C association work.
No gap is bridged, segmented implicitly, or interpolated.

All timestamps are integer board uptime milliseconds. With at least eight samples,
derive the mean interval from the first/last timestamps. As in PR #4, both largest
per-interval and cumulative grid deviation must be <= `max(1 ms, 2% of mean
interval)`. Reject before filtering if either fails. Cadence and deviations are
reported, and the filter sample rate is derived from that measured interval.
Admitted small quantization jitter is an explicit approximation, not resampling.

Identical real SOS coefficients filter I and Q separately, implemented as one
complex `sosfilt` call per mode. No magnitude or projected waveform is filtered.
Baseline is an unchanged copy. State starts once, using `sosfilt_zi * first_z`,
the equilibrium for a constant extension of the first observed complex sample.
This is a documented assumption about pre-recording history; a noisy first sample
can cause startup artifacts. Low-pass preserves constant input; band-pass removes
DC. No `filtfilt`, future samples, phase alignment, delay compensation, or state
reset at analysis-window boundaries is used. Filtering happens over the entire
accepted recording before metrics or plots.

The existing `project_iq` from PR #4 is reused only for descriptive projected RMS
**after** filtering, independently per mode; that scalar is not a shared-axis
reconstruction-error measurement. The unfiltered PR #4 temporal report is also
included as `source.baseline_temporal`, along with whole-file quality. Existing
parser, quality, visualization, signal, model, and runtime contracts are unchanged.

### Seeded synthetic ground truth and metrics

The 60-second test case uses actual quantized sample timestamps, clean unit-amplitude
0.3 Hz sine motion along complex direction `exp(0.6j)`, and exact movement cessation
at 40 s (a zero crossing). Observed I/Q adds 0.5-amplitude 0.03 Hz drift along
`exp(1.2j)` and independent I/Q Gaussian noise, sigma 0.6 per component, seeded
with NumPy `default_rng` (default seed 7). Drift/noise continue after cessation.
Ground truth deliberately excludes both. Sample rate and seed are configurable;
rates 10 and 20 Hz were measured, with 12.5 Hz additionally tested. Fixed noise
variance per sample at different rates is not a matched continuous-noise model,
so cross-rate differences do not establish a sampling-rate advantage.

All modes use identical evaluation intervals. Metrics are not selected from a
favorable post-hoc lag or from a spectral concentration score:

| Metric | Definition |
| --- | --- |
| `snr_db` | `10*log10(mean(abs(clean)^2) / mean(abs(output-clean)^2))`, over 10 <= t < 40 s. This is reference/error SNR including drift, noise, attenuation and phase error, not isolated stochastic-noise power. Null for zero error/reference power instead of JSON infinity. |
| `snr_improvement_db` | Mode SNR minus baseline SNR on that same interval. |
| `reconstruction_rmse` | `sqrt(mean(abs(output-clean)^2))` on 10-40 s, with **no delay compensation**. |
| `signal_attenuation_db` | Negative 20 log10 gain at the known 0.3 Hz motion; fit sine/cosine plus constant to the **noise-free filtered clean response** and clean reference over 10-40 s. Positive means attenuation; negative would mean gain. |
| `phase_delay_sec` | Signed, wrapped fitted phase difference divided by `-2*pi*0.3`. A narrowband phase lead can be negative despite strict causality; this is not onset-detection latency. |
| `group_delay_sec` | Theoretical `-d phase(H)/d angular_frequency` at 0.3 Hz, using SOS response at 0.3 +/- 0.0003 Hz. Not an event-detection latency. |
| `post_cessation_residual_rms` | RMS of the noise-free clean-only filter response on 40 <= t < 45 s. Isolates memory/ringing from continuing injected noise and drift. |
| `post_cessation_peak_abs` | Peak magnitude of that clean-only response over 40-45 s. |
| `post_cessation_observed_rms` | RMS of the noisy/drifting output on 40-45 s; includes nuisance as well as memory. |
| `settling_time_sec` | First sample after the last post-cessation clean-only magnitude > 0.01 (1% of unit motion). All remaining observed samples must stay below; null if not settled by recording end. No claim about unobserved future. |
| `startup_rmse` | Noisy output versus clean RMS error over 0 <= t < 10 s; exposes the startup interval omitted from the main active-motion score. |

The clean-only response used for attenuation and ringing has the same causal
filter/initial-state rule as the noisy output. It is a separate ground-truth
experiment, never substituted for observed output in the SNR calculation.

Actual seed-7 measurements, order 2 / LP 1.5 Hz / BP 0.1-1.5 Hz:

| Rate | Mode | Reference/error SNR dB | Improvement dB | RMSE | Motion attenuation dB | Phase delay s | Group delay s | Clean residual RMS | Settling s |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 10 Hz | baseline | -1.8847 | 0 | 0.87846 | 0 | 0 | 0 | 0 | 0 |
| 10 Hz | lowpass | 1.4096 | 3.2943 | 0.60118 | 0.00514 | 0.14075 | 0.14467 | 0.03999 | 0.5 |
| 10 Hz | bandpass | 3.1253 | 5.0100 | 0.49342 | 0.00319 | -0.11846 | 0.42659 | 0.19937 | 8.1 |
| 20 Hz | baseline | -2.2684 | 0 | 0.91813 | 0 | 0 | 0 | 0 | 0 |
| 20 Hz | lowpass | 1.9188 | 4.1871 | 0.56695 | 0.00646 | 0.14923 | 0.15306 | 0.03612 | 0.5 |
| 20 Hz | bandpass | 5.8463 | 8.1147 | 0.36072 | 0.00262 | -0.11053 | 0.43465 | 0.19923 | 8.1 |

Thus lower active-motion error can coexist with longer memory after movement
stops. At 20 Hz the clean-only residual peaks are about 0.2774 (LP) and 0.4660
(BP), versus 0 baseline. Startup RMSE is 0.9261/0.5390/0.4133 for baseline/LP/BP.
These findings depend on this motion/drift/noise mix, seed, cessation phase, and
chosen cutoffs. They do not pick a universally better filter.

### Descriptive real-recording results

The same parameters and bin 9 (48.75 cm) were used for all three recordings;
sample rates were measured individually (about 16.641 Hz). All 999/1000/996 D
frames respectively pass cadence/integrity/configuration checks. No filenames
are used to label exact breathing/hold/cessation intervals. Truth-dependent metrics
above are explicitly null for real recordings: no SNR improvement, attenuation,
reconstruction accuracy, or cessation-ring time is inferred.

| Sample | Mode | Whole-file complex variation RMS | RMS change from original I/Q | Power fraction above 1.5 Hz |
| --- | --- | ---: | ---: | ---: |
| breathing | baseline | 377.4619 | 0 | 0.31378 |
| breathing | lowpass | 307.9223 | 330.2131 | 0.08104 |
| breathing | bandpass | 285.5036 | 349.3846 | 0.08204 |
| hold | baseline | 30.6705 | 0 | 0.02632 |
| hold | lowpass | 29.8985 | 11.1492 | 0.00560 |
| hold | bandpass | 11.3222 | 38.7146 | 0.04380 |
| leave | baseline | 8.2581 | 0 | 0.65373 |
| leave | lowpass | 6.1620 | 7.2533 | 0.15791 |
| leave | bandpass | 5.3379 | 9.1808 | 0.16871 |

Variation RMS is centered complex RMS over the entire output; change RMS is
`sqrt(mean(abs(output-original)^2))` including DC/phase changes, **not** distortion
against truth. `mean_i`/`mean_q` expose DC removal. The high-frequency fraction
uses centered Hann one-sided I and Q PSDs summed together, DC excluded; numerator
is bins above the configured LP cutoff, denominator all positive-frequency bins.
For no measurable power the fraction is null. A fraction can increase after BP
because its low-frequency denominator decreased (as in hold), so it is not a
noise reduction score. Useful motion can also be removed. Fixed-bin comparisons
have distance/reflector confounders and these three unlabeled captures cannot
select a filter or establish improved apnea detection or AI/medical accuracy.

### Reports, API, and future on-device implications

The PNG uses Agg with no GUI: three side-by-side I/Q panels with common vertical
limits, before/after centered I/Q spectra, and a metric table. Synthetic reports
also include a known-cessation I-component zoom and clean-I overlay. The observed
zoom still contains drift/noise; the table's clean-only residual explicitly
isolates filter memory. All raw time coordinates are preserved. Plotted PSD is
summed I/Q power, never a silently substituted magnitude/projection spectrum.

```python
from ml.analysis.protocol import read_capture
from ml.analysis.denoise import FilterParameters, benchmark_capture, benchmark_synthetic, render_report

settings = FilterParameters(order=2, lowpass_hz=1.5, band_low_hz=0.1, band_high_hz=1.5)
experiment = benchmark_synthetic(sample_hz=20, seed=7, parameters=settings)
real = benchmark_capture(read_capture("recordings/samples/hold.sample.csv"), 9, settings)
metrics = experiment.to_dict()  # detached JSON-compatible dictionary
render_report(experiment, "synthetic20.png")
```

`DenoisingComparison` retains observed elapsed times, mode arrays, optional clean
truth, and the numeric report. Report keys include source/seed/selected distance,
observed count, original first/last t_ms, cadence diagnostics, parameters, explicit
signal-domain/initial-state descriptions, common evaluation intervals, and per-mode
metrics. Baseline temporal/quality reports appear only for real captures. JSON
has stable sorted keys and no generated date/time; NumPy/SciPy versions can affect
floating-point or seeded-generator reproducibility across environments. PNG
appearance can additionally depend on Matplotlib/fonts. Five actual CLI PNG/JSON
reports were generated for review (10/20 Hz synthetic plus three samples) and
kept outside tracked source; no large binary artifacts are committed.

`ml/features.py` intentionally uses simple, stateless-window arithmetic for later
on-device execution. Adding these IIR filters there would require continuous
state per antenna/bin/I/Q channel, reconnect/reset handling, cadence validation,
and a specified initialization policy. A direct-form SOS has two states and
roughly five multiplies/four additions per real sample; filtering both I and Q
doubles that arithmetic. Default LP uses one SOS and BP two, so each bin needs
four/eight real state values respectively. These are structural operation/state
counts, not measured runtime benchmarks. Float64 offline behavior does not
validate embedded precision, memory, CPU cost, or quantization stability.

Causal delay is frequency-dependent, and low cutoff state can keep oscillating
after motion stops. This can matter to alarm timing and to CNN feature
distributions. Any future adoption would need labeled evaluation, training/input
consistency, state reset tests, and target-board cost/precision measurements.
This PR makes none of those runtime/model changes and automatically selects no
winning filter. Processing stays in memory and no interpolation, model training,
classifier, medical judgment, 3D work, or live dashboard is added.

## CNN sensitivity to denoising

`ml.analysis.model_impact` measures **feature drift** and **prediction drift**
when the same exported 1D `breath_slot` CNN sees original, causal low-pass, and
causal band-pass I/Q. It does not train, replace, or modify the model. Here
"sensitivity" means response to an input perturbation, not the labeled
classification statistic. No LiteRT, TensorFlow, GPU, or radar is required.

```sh
python -m ml.analysis.model_impact recordings/samples/breathing.sample.csv --bin 9 --json
python -m ml.analysis.model_impact recordings/samples/hold.sample.csv --bin 9 --hop 8 --max-windows 12 --json
python -m ml.analysis.model_impact recordings/samples/leave.sample.csv --bin 9 --output leave_model_impact.png --json > leave_model_impact.json
```

Without `--json`, a descriptive summary is printed. PNG output is optional,
headless, and never created automatically. JSON goes to stdout; a PNG success
message goes to stderr in JSON mode. Existing PNGs require `--force`. Invalid
capture/options, missing input/model artifacts, and output errors produce stderr
and status 2. Validation failures create no PNG. Parent directories must exist.

### Model input compatibility and continuous filtering

The analysis reuses parser/quality validation and PR #5 cadence checks, rejecting
malformed rows, seq gaps, resets, repeated/backward time, irregular timing, and
missing/mismatched/changing configuration. In addition, it requires the trained
model's `C,15,37.5,24,10` configuration. The range grid is derived from existing
`ml.features` constants/`slot_cm`; scale 10 is required because training/live D
readers divide by `SCALE=10`. Accepting another scale while claiming equivalence
to those readers would be misleading. At least `WINDOW=200` observed D samples
are required. No interpolation or geometry conversion is performed.

`CaptureFrame.z()` supplies scaled complex I/Q. For **all 24 bins**, PR #5's
`filter_iq` is applied once over the entire accepted recording with identical
settings and measured sample rate. State is initialized once per bin/mode using
the first-sample steady-state rule, never at individual CNN windows. Default
settings remain order 2, LP 1.5 Hz, BP 0.1-1.5 Hz; matching `--order`,
`--lowpass-hz`, `--band-low-hz`, and `--band-high-hz` flags are supported.

The unchanged `ml.features.window_features()` produces each condition's tensor:
24 bins x 100 steps x 15 interleaved channels from the same 200 sample indices.
This preserves mean subtraction/projection, five-bin neighborhood normalization,
level/amount transforms, edge padding and paired-sample averaging. Tests compare
baseline arrays/features exactly with `ml.features.read_csv()` plus the existing
feature function, verify all-bin filtering, and distinguish continuous filtering
from an incorrectly reset late window. No alternate feature definition is used.

`--bin` selects only the displayed curve and per-bin summary (default 9, 48.75 cm).
It **does not restrict filtering or inference**: all 24 bins, including neighboring
context bins, remain in every evaluated window. The existing 1D model is loaded
explicitly via `ml.runtime.Model("breath_slot")`, not `ml.runtime.load`, so the
same full-precision NumPy weights are used for all three conditions even when
LiteRT is installed. Results are not asserted identical to live int8 inference.
No 2D state model, alarm logic, or complete live pipeline is reproduced.

### Bounded, aligned evaluation policy

`--hop` is a positive integer **sample count**, default 8, matching the existing
feature-window hop and live 1D evaluation schedule. Candidate ends are exclusive
indices `200, 200+hop, ... <= observed_D_count`. For each end, the interval is
`[end-200, end)`, with actual source line numbers and first/last included uptime
recorded. `evaluation_t_ms` is the last included D's board uptime, not Unix time.

`--max-windows` is a positive cap, default 12. For M candidates choose
K = min(M, max_windows) ranks, with rank j = floor(j*(M-1)/(K-1)). K > 1 includes
first and last candidates; K=1 selects the first. No randomness or resampling is
used. Selection is shared by all conditions. Increasing the cap to at least M
evaluates every candidate window; this remains overlapping-window inference,
not independently labeled trials. Reports explicitly include M, K, hop, the
selected indices, 24 evaluated bins, and K*24 predictions **per condition**.
Filtering the complete recording does not mean all candidate windows were inferred.

The default cap bounds feature/inference tensors to 12 x 24 x 100 x 15 per
condition; complete raw/filtered arrays are still held in memory. The first
evaluated feature window includes causal-filter startup effects; no warm-up is
silently discarded. Choosing another hop/cap/bin or filter setting can change
the summaries. Deterministic spread sampling is a cost policy, not proof that
the subset represents every rare event in a recording.

### Metrics and report fields

All differences compare a condition against the **unfiltered** tensor/predictions
at identical windows/bins; baseline differences and flips are zero by definition.

- Feature metrics are the mean and 95th percentile of elementwise absolute
  differences, plus element counts. They are also split by the existing channel
  families (`wave = channels 0::3`, `level = 1::3`, `amount = 2::3`). Counts include
  all five neighborhood channels and padded edge context, not just the central
  bin's scalar. All-bin and selected-bin summaries are separate. Differences
  preserve the model's numeric channel scales and do not measure correctness.
- Prediction metrics are mean and 95th percentile absolute probability
  difference, mean probability, positive counts, flip counts/percent, and up/down
  flips. The convention is `p >= 0.5`, matching the existing `ml/train.py`
  threshold. A flip is disagreement with the unfiltered decision, not a corrected
  prediction. Percent = 100*flips/(evaluated windows*bins); selected-bin percent
  instead uses evaluated-window count. P95 is an elementwise percentile, not a
  confidence interval. A few large changes can make MAE exceed P95.

JSON top-level keys are `model`, `parameters`, `selected_bin`, `recording`,
`evaluation`, `feature_drift`, `prediction_drift`, `windows`, and `limitations`.
`model` names the runtime/export representation and SHA-256 of the exact JSON/NPZ
artifact bytes loaded. `--model-dir` can select an exported artifact directory;
the default is the committed `ml/data/models`, and missing/malformed artifacts
fail explicitly. Changing directories does not switch models between conditions.
JSON-layer artifact hashes can reflect checkout newline conversions; binary
weight hashes identify the evaluated weights. Recording quality/cadence and
explicit candidate coverage remain separate from feature/prediction drift.

Every window records start/exclusive-end sample indices, source line bounds,
actual `start_t_ms` and `evaluation_t_ms`, all 24 probabilities for each condition,
and the selected-bin probabilities. No generated wall-clock metadata appears.
The optional PNG plots selected-bin probabilities against actual evaluation
times on a fixed 0-1 scale; marker-connecting lines are visual guides, not inferred
intermediate outputs. It includes both selected/all-bin metrics and coverage.
Near-zero/one probabilities can overlap visually; JSON retains full precision.

```python
from ml.analysis.protocol import read_capture
from ml.analysis.model_impact import analyze_model_impact, prepare_features

capture = read_capture("recordings/samples/breathing.sample.csv")
report = analyze_model_impact(capture, bin_index=9, hop_samples=8, max_windows=12)
# prepare_features exposes aligned feature tensors/whole-recording filtered I/Q
# for numeric inspection, without running or changing the model.
```

### Actual public-sample results

The following use committed NumPy `breath_slot` weights (NPZ SHA-256
`4b51fb3ce7fffa14a113a6f39a313e07e726f474e83d5a3b53b05949e6d643fc`), defaults
above, and selected bin 9. Breathing/hold/leave contain 999/1000/996 D frames and
100/101/100 candidate windows. **Only 12 windows per file** were evaluated: 288
probabilities per condition over all bins, not a complete candidate-window run.
Each all-bin feature comparison covers 432,000 elements (144,000 per family).

All-bin feature drift relative to original (absolute difference):

| Recording | Filter | MAE | P95 | Wave MAE | Level MAE | Amount MAE |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| breathing | LP | 0.195707 | 0.731095 | 0.327054 | 0.204529 | 0.055537 |
| breathing | BP | 0.221193 | 0.817426 | 0.369776 | 0.229315 | 0.064488 |
| hold | LP | 0.146832 | 0.570779 | 0.237273 | 0.163254 | 0.039970 |
| hold | BP | 0.251597 | 0.940362 | 0.429227 | 0.253910 | 0.071654 |
| leave | LP | 0.231263 | 0.907098 | 0.448751 | 0.207716 | 0.037322 |
| leave | BP | 0.295926 | 1.128229 | 0.570841 | 0.264282 | 0.052655 |

All-bin prediction drift (288 predictions per row):

| Recording | Filter | Probability MAE | P95 | Flips | Percent | Up / down |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| breathing | LP | 0.013206 | 0.061805 | 3 | 1.0417% | 2 / 1 |
| breathing | BP | 0.020405 | 0.125248 | 6 | 2.0833% | 4 / 2 |
| hold | LP | 0.015606 | 0.047268 | 4 | 1.3889% | 2 / 2 |
| hold | BP | 0.035632 | 0.146148 | 11 | 3.8194% | 9 / 2 |
| leave | LP | 0.010538 | 0.003546 | 3 | 1.0417% | 1 / 2 |
| leave | BP | 0.010343 | 0.003372 | 3 | 1.0417% | 2 / 1 |

Selected bin 9 (12 predictions per condition) has zero threshold flips in every
file, but nonzero probability drift. Its mean probabilities are:

| Recording | Original | LP | BP |
| --- | ---: | ---: | ---: |
| breathing | 0.999843 | 0.999478 | 0.999803 |
| hold | 0.002065 | 0.001703 | 0.001433 |
| leave | 0.000123 | 0.000062 | 0.000093 |

These near-zero/one values explain visually overlapping selected-bin curves;
they do not establish calibration, robustness, or correctness across other bins.
The filenames supply no complete per-window labels, and fixed-bin comparisons
also have reflector/distance confounders. Feature drift, prediction drift and
labeled accuracy are different quantities. No accuracy, precision, recall,
classification sensitivity/specificity, corrected predictions, improved apnea
detection, or best filter is claimed. A future labeled evaluation must decide
whether the observed feature/probability/threshold changes help or harm detection.
No retraining, weight changes, live model inference changes, alarm logic, firmware,
training pipeline, or React dashboard change is included.

## Validation

```sh
python -m unittest discover -s ml/analysis/tests -t . -v
python -m unittest discover -s pc/tests -t pc -v
```
