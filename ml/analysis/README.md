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
source line numbers, without payload validation or interpretation. No sequence
quality analysis, model changes, alarms, visualization, Parquet, or reports are
implemented here.

## Validation

```sh
python -m unittest discover -s ml/analysis/tests -t . -v
python -m unittest discover -s pc/tests -t pc -v
```
