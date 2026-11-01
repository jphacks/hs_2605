"""Segment-safe offline Hann STFT; spectral peaks are not breathing labels."""
from __future__ import annotations

from copy import deepcopy
import math

import numpy as np
import scipy
from scipy.signal import stft

from .report import ReportDataset, prepare_blocks
from .signal import project_iq, sampling_cadence


def build_spectrogram(dataset: ReportDataset, bin_index=0, *, window_sec=12, hop_sec=3):
    """Use complete measured-cadence windows, no padding or gap interpolation.

    Power is one-sided PSD in radar value units squared/Hz. The projection axis
    is fixed over each continuous segment using signal.project_iq, not a CNN.
    """
    if type(bin_index) is not int or bin_index < 0:
        raise ValueError("Bin index must be a nonnegative integer")
    if any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in (window_sec, hop_sec)) or hop_sec > window_sec:
        raise ValueError("Window and hop seconds must be finite and positive, with hop <= window")
    segments, offset = [], 0
    for parent_index, parent in enumerate(prepare_blocks(dataset, 0), 1):
        count, data = parent["frame_count"], parent["data"]
        rows = dataset.frames[offset:offset+count]
        if data is not None and bin_index >= data.config.n:
            raise ValueError(f"Bin {bin_index} outside parent block {parent_index}'s 0..{data.config.n-1} range")
        for trace_index, section in enumerate(data.trace_segments if data is not None else (slice(0, count),), 1):
            observed = rows[section]
            entry = dict(segment_index=len(segments)+1, parent_block_index=parent_index,
                         trace_segment_index=trace_index if data is not None else None,
                         frame_row_start=offset+section.start, frame_row_end_exclusive=offset+section.stop,
                         source_lines=[r["source_line"] for r in observed],
                         observed_samples=len(observed), message_type=observed[0]["message_type"],
                         sequence_start=observed[0]["seq"], sequence_end=observed[-1]["seq"],
                         reference_board_uptime_ms=observed[0]["board_uptime_ms"],
                         last_board_uptime_ms=observed[-1]["board_uptime_ms"],
                         selected_antenna=0, recorded_antenna_count=observed[0]["antenna_count"],
                         value_divisor=observed[0]["value_divisor"], recorded_config_scale=observed[0]["config_scale"],
                         distance_cm=float(data.distances_cm[bin_index]) if data is not None else None,
                         cadence=None, sampling_rate_estimate_hz=None, window_samples=None, hop_samples=None,
                         status="unavailable", unavailable_reason=parent["reason"],
                         covered_frames=0, trailing_unwindowed_frames=len(observed),
                         frequency_hz=None, windows=None, power_density=None)
            segments.append(entry)
            if data is None:
                continue
            if len(observed) < 8:
                entry["unavailable_reason"] = "insufficient_samples (minimum 8)"
                continue
            times = [r["board_uptime_ms"] for r in observed]
            cadence = sampling_cadence(times)
            fs = 1000/cadence["sampling_interval_ms"]
            entry.update(cadence=cadence, sampling_rate_estimate_hz=fs)
            tolerance = cadence["interval_tolerance_ms"]
            if any(cadence[k] > tolerance for k in ("max_interval_deviation_ms", "max_grid_deviation_ms")):
                entry["unavailable_reason"] = "irregular_timing (local or cumulative deviation exceeds tolerance)"
                continue
            if not all(math.isfinite(v*fs) for v in (window_sec, hop_sec)):
                raise ValueError("Requested window/hop sample count cannot be represented")
            n, hop = int(math.floor(window_sec*fs+.5)), int(math.floor(hop_sec*fs+.5))
            entry.update(window_samples=n, hop_samples=hop)
            if n < 8 or hop < 1:
                entry["unavailable_reason"] = "requested_window_or_hop_too_short_for_cadence"
                continue
            if len(observed) < n:
                entry["unavailable_reason"] = "insufficient_samples_for_complete_window"
                continue
            with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
                wave = project_iq(data.z[section, bin_index])
                if not np.isfinite(wave).all():
                    entry["unavailable_reason"] = "non_finite_projection"
                    continue
                frequencies, _, coefficients = stft(wave, fs=fs, window="hann", nperseg=n,
                    noverlap=n-hop, nfft=n, detrend=False, return_onesided=True,
                    boundary=None, padded=False, scaling="psd")
                power = np.abs(coefficients)**2
                # STFT returns one-sided coefficients without PSD energy doubling.
                power[1:-1 if n % 2 == 0 else None] *= 2
            if not np.isfinite(power).all():
                entry["unavailable_reason"] = "non_finite_power_density"
                continue
            starts = list(range(0, len(observed)-n+1, hop))
            windows = []
            for start in starts:
                selected = observed[start:start+n]
                windows.append(dict(frame_row_start=offset+section.start+start,
                    frame_row_end_exclusive=offset+section.start+start+n,
                    source_line_start=selected[0]["source_line"], source_line_end=selected[-1]["source_line"],
                    first_board_uptime_ms=selected[0]["board_uptime_ms"], last_board_uptime_ms=selected[-1]["board_uptime_ms"],
                    center_offset_s=float(np.mean([r["board_uptime_ms"]-times[0] for r in selected]))/1000))
            entry.update(status="available", unavailable_reason=None, frequency_hz=frequencies.tolist(),
                         windows=windows, power_density=power.tolist(), covered_frames=starts[-1]+n,
                         trailing_unwindowed_frames=len(observed)-(starts[-1]+n), constant_signal=bool(np.all(wave == 0)),
                         effective_window_sec=n/fs, effective_hop_sec=hop/fs, frequency_spacing_hz=fs/n)
        offset += count
    m = dataset.manifest
    covered = sum(s["covered_frames"] for s in segments)
    return dict(format="ibuki.radar.spectrogram", spectrogram_version=1, dataset_schema_version=m["schema_version"],
        bin_index=bin_index, selected_antenna=0,
        provenance=deepcopy({k:m[k] for k in ("session","source_type","source_type_provenance","scenario","input","capture_configuration")}),
        verified_artifact_hashes=deepcopy(m["artifacts"]), data_quality=deepcopy(dataset.quality),
        parameters=dict(window_sec=window_sec, hop_sec=hop_sec, window="periodic Hann", nfft="window_samples",
                        boundary_padding=False, tail_padding=False, detrend="segment mean/projection only; no per-window detrend",
                        cadence="(last recorded uptime - first)/(N-1); same tolerance policy as signal.py",
                        timing_tolerance="local and cumulative error <= max(1 ms, 2% of measured interval)",
                        sample_rounding="nearest integer, half up", minimum_window_samples=8),
        assumptions=dict(signal="signal.project_iq applied once per continuous segment, fixed segment axis; antenna 0",
            units="one-sided PSD, radar value units squared/Hz; D / observed C.scale, E already scaled",
            time="window center = mean of actual recorded uptimes, expressed as offset from segment reference; not Unix time",
            power="X=unnormalized Hann FFT; PSD=abs(X)**2/(fs*sum(Hann**2)); positive non-Nyquist bins doubled; DC retained",
            segmentation="report.prepare_blocks plus visualization trace_segments; no spectra across discontinuities",
            limits="spectral peaks are not automatically breathing frequencies; no classifier, SNR, medical or denoising validation",
            geometry="compatible observed C only; delayed first-C backfill remains an ingestion assumption"),
        coverage=dict(exported_frames=len(dataset.frames), spectral_covered_frames=covered,
            unavailable_or_unwindowed_frames=len(dataset.frames)-covered, segment_count=len(segments),
            available_segments=sum(s["status"] == "available" for s in segments)),
        unavailable_reason="No exported frames" if not segments else None,
        versions=dict(numpy=np.__version__, scipy=scipy.__version__), segments=segments)


def build_figure(result):
    """Flat PSD cells at measured window centers, separate axes across boundaries."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.colors import Normalize
    from matplotlib.figure import Figure

    segments = result["segments"]
    figure = Figure(figsize=(11, max(4.5, 4*len(segments))), layout="constrained")
    FigureCanvasAgg(figure)
    axes = figure.subplots(max(1,len(segments)), 1, squeeze=False)[:,0]
    floor = 1e-12
    maximum = max((max(max(row) for row in s["power_density"]) for s in segments if s["status"] == "available"), default=floor)
    norm = Normalize(-120, max(-119, 10*math.log10(max(floor, maximum))))
    if not segments:
        axes[0].axis("off")
        axes[0].text(.05,.5,"STFT unavailable: no exported frames",transform=axes[0].transAxes)
    for ax, segment in zip(axes, segments):
        label = f"Segment {segment['segment_index']} ({segment['message_type']}), {segment['observed_samples']} observed samples"
        if segment["status"] != "available":
            ax.axis("off")
            ax.text(.02,.75,label,transform=ax.transAxes,fontsize=12)
            from textwrap import fill
            ax.text(.02,.5,fill("STFT unavailable: "+segment["unavailable_reason"],width=90),transform=ax.transAxes)
            continue
        centers = np.array([w["center_offset_s"] for w in segment["windows"]])
        frequencies = np.asarray(segment["frequency_hz"])
        power = np.asarray(segment["power_density"])
        # Cell edges are display boundaries, not interpolated measurements/power.
        if len(centers) > 1:
            half = np.diff(centers)/2
            time_edges = np.r_[centers[0]-half[0], centers[:-1]+half, centers[-1]+half[-1]]
        else:
            window = segment["windows"][0]
            reference = segment["reference_board_uptime_ms"]
            time_edges = [(window["first_board_uptime_ms"]-reference)/1000,
                          (window["last_board_uptime_ms"]-reference)/1000]
        df = segment["frequency_spacing_hz"]
        freq_edges = np.r_[0, (frequencies[:-1]+frequencies[1:])/2, frequencies[-1]+df/2]
        freq_edges[-1] = min(freq_edges[-1], segment["sampling_rate_estimate_hz"]/2)
        image = ax.pcolormesh(time_edges, freq_edges, 10*np.log10(np.maximum(power,floor)),
                             shading="flat",cmap="magma",norm=norm)
        figure.colorbar(image, ax=ax, label="PSD (dB re 1 radar unit²/Hz)")
        ax.set_ylabel("Frequency (Hz)")
        ax.set_xlabel(f"Window center offset (s), observed board uptime reference {segment['reference_board_uptime_ms']} ms")
        ax.set_title(label+f"; bin {result['bin_index']} at {segment['distance_cm']:g} cm\n"
                     f"Measured fs={segment['sampling_rate_estimate_hz']:.6g} Hz; Hann N={segment['window_samples']}; df={df:.6g} Hz")
    figure.suptitle(f"Offline STFT - antenna 0, bin {result['bin_index']}\nDisplay floor -120 dB; exploratory power, not breathing or medical validation",fontsize=13)
    return figure


def export_spectrogram(directory, output, bin_index=0, *, window_sec=12, hop_sec=3, force=False):
    """Render both outputs before writing; protect canonical input and both paths."""
    import hashlib
    from io import BytesIO
    import json
    import os
    from pathlib import Path
    import tempfile
    import matplotlib
    from .report import load_dataset

    root, requested = Path(directory).resolve(), Path(output)
    if requested.suffix.lower() != ".png":
        raise ValueError("Spectrogram output must have a .png extension")
    paths = (requested, requested.with_suffix(".json"))
    for path in paths:
        if path.is_symlink():
            raise ValueError("Spectrogram output must not be a symlink")
        resolved = path.resolve()
        if resolved == root or root in resolved.parents:
            raise ValueError("Spectrogram outputs must be outside the canonical dataset directory")
        if path.exists() and not force:
            raise FileExistsError(f"Output already exists: {path}; use --force")
    result = build_spectrogram(load_dataset(root),bin_index,window_sec=window_sec,hop_sec=hop_sec)
    figure = build_figure(result)
    try:
        with BytesIO() as buffer:
            figure.savefig(buffer,format="png",dpi=120,metadata={"Software":"IBUKI offline analysis"})
            png = buffer.getvalue()
    finally:
        figure.clear()
    result["rendering"] = dict(matplotlib=matplotlib.__version__,power_floor=1e-12,
        color="10*log10(max(PSD,1e-12)/(1 radar value unit^2/Hz))",png_sha256=hashlib.sha256(png).hexdigest(),
        cells="flat colors at measured window centers; midpoint edges are display only, not interpolated measurements")
    encoded = (json.dumps(result,sort_keys=True,indent=2,allow_nan=False)+"\n").encode("utf-8")
    created = []
    try:
        for path, content in zip(paths,(png,encoded)):
            if not force:
                with path.open("xb") as stream:
                    created.append(path)
                    stream.write(content)
            else:
                with tempfile.NamedTemporaryFile(dir=path.resolve().parent,prefix=".ibuki-stft-",delete=False) as stream:
                    stage = Path(stream.name)
                    try:
                        stream.write(content)
                    except OSError:
                        stream.close()
                        stage.unlink()
                        raise
                try:
                    os.replace(stage,path)
                finally:
                    if stage.exists():
                        stage.unlink()
    except OSError:
        for path in created:
            path.unlink()
        raise
    return result


def main(argv=None):
    import argparse
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset",help="canonical schema-v1 directory, never raw CSV")
    parser.add_argument("--bin",type=int,default=0,dest="bin_index")
    parser.add_argument("--output",required=True,help="PNG path; matching JSON is always written beside it")
    parser.add_argument("--window-sec",type=float,default=12)
    parser.add_argument("--hop-sec",type=float,default=3)
    parser.add_argument("--json",action="store_true",help="also emit the same results JSON to stdout")
    parser.add_argument("--force",action="store_true")
    args = parser.parse_args(argv)
    try:
        result = export_spectrogram(args.dataset,args.output,args.bin_index,window_sec=args.window_sec,
                                    hop_sec=args.hop_sec,force=args.force)
    except (OSError,ValueError,ImportError,OverflowError) as exc:
        parser.exit(2,f"error: {exc}\n")
    if args.json:
        print(json.dumps(result,sort_keys=True,indent=2,allow_nan=False))
    else:
        print(f"Saved spectrogram PNG and JSON: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
