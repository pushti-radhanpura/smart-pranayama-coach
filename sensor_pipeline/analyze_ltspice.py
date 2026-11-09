"""
analyze_ltspice.py
==================
Analysis pipeline for capacitive respiration sensor data from LTspice.
Demodulates the 10 kHz carrier, recovers capacitance, plate overlap,
belt expansion, breath volume, and flow, and extracts the 5 key breathing parameters:
  1. Inhale : Hold : Exhale ratio (seconds and normalized 1 : x : y)
  2. Breaths per minute (BPM)
  3. Tidal volume (mL) [assumed k = 50 mL/mm calibration]
  4. Peak expiratory flow & Mean expiratory flow (mL/s)
  5. Pattern endurance / sustained time (s) & never_broke flag

Provides importable functions with zero import-time side effects, plus a CLI.
"""
from __future__ import annotations

import io
import json
import os
import sys
from typing import Any

import numpy as np
from scipy.interpolate import CubicSpline
from scipy.signal import butter, find_peaks, savgol_filter, sosfiltfilt

# Sensor / circuit constants (hardware specifications, matched with cap_model.py)
EPS0 = 8.854e-12          # Permittivity of free space (F/m)
EPS_R = 3.0               # Relative dielectric permittivity
WIDTH = 20e-3             # Plate width (m)
OVERLAP0 = 40e-3          # Initial overlap (m)
GAP = 1e-3                # Plate separation (m)
ETA = 1.0                 # Mechanical transmission factor
R = 1e6                   # Divider resistor (1 MOhm)
VIN = 5.0                 # Carrier input amplitude (V)
F_CARRIER = 10e3          # Carrier frequency (10 kHz)
OMEGA = 2 * np.pi * F_CARRIER
K_ML_PER_MM = 50.0        # Assumed chest belt calibration: 1 mm expansion = 50 mL volume


def _extract_parameters(
    tt: np.ndarray,
    exp_rec: np.ndarray,
    vol_ml: np.ndarray,
    flow: np.ndarray,
    warnings: list[str] | None = None,
    extra_assumptions: list[str] | None = None,
) -> dict[str, Any]:
    """Helper to detect breath peaks/plateaus and calculate the 5 core respiratory parameters."""
    warnings_list = list(warnings or [])
    assumptions = [
        "k = 50 mL per mm of belt expansion (assumed calibration)",
        "Mechanical transmission factor eta = 1.0 (ideal string transmission)",
    ]
    if extra_assumptions:
        assumptions.extend(extra_assumptions)

    FS_W = 100.0
    sm = savgol_filter(exp_rec, 31, 3)
    slope = savgol_filter(sm, 31, 3, deriv=1, delta=1 / FS_W)
    prom = 0.25 * (sm.max() - sm.min())
    tr, _ = find_peaks(-sm, prominence=prom, distance=100)
    tr = np.concatenate(([0], tr))
    if sm[-1] < sm.min() + 0.1 * (sm.max() - sm.min()):
        tr = np.concatenate((tr, [len(sm) - 1]))
    base = float(sm[:20].mean())

    breaths = []
    for a, b in zip(tr[:-1], tr[1:]):
        ipk = a + int(np.argmax(sm[a:b]))
        thr = 0.08 * float(np.max(np.abs(slope[a:b])))
        flat = np.where(np.abs(slope[a:b]) < thr)[0] + a
        flat = flat[(flat > a + 30) & (flat < b - 30) & (sm[flat] > base + 0.8 * (sm[ipk] - base))]
        if flat.size and (flat[-1] - flat[0]) / FS_W > 0.5:
            h0, h1 = int(flat[0]), int(flat[-1])
        else:
            h0 = h1 = int(ipk)

        t0 = float(tt[a])
        T = float(tt[b] - tt[a])
        tin = float(tt[h0] - tt[a])
        hold = float(tt[h1] - tt[h0])
        tex = float(tt[b] - tt[h1])
        amp = float(sm[ipk] - base)
        vt = float(amp * K_ML_PER_MM)
        pef = float(-flow[a:b].min())
        breaths.append({
            "t0": round(t0, 2),
            "T": round(T, 2),
            "tin": round(tin, 2),
            "hold": round(hold, 2),
            "tex": round(tex, 2),
            "amp_mm": round(amp, 2),
            "vt_ml": round(vt, 1),
            "pef_ml_s": round(pef, 1),
        })

    n_breaths = len(breaths)
    if n_breaths < 3:
        raise ValueError(
            f"Fewer than 3 breaths detected in recording ({n_breaths} found); "
            "insufficient data to compute reliable parameters."
        )

    B = {k: np.array([x[k] for x in breaths]) for k in breaths[0]}
    ref = slice(0, 3)
    tin0 = float(np.median(B["tin"][ref]))
    th0 = float(np.median(B["hold"][ref]))
    tex0 = float(np.median(B["tex"][ref]))
    bpm = float(60.0 / np.median(B["T"][ref]))
    tv0 = float(np.median(B["vt_ml"][ref]))
    pef0 = float(np.median(B["pef_ml_s"][ref]))
    mef0 = float(tv0 / tex0) if tex0 > 0 else 0.0

    dev = (np.abs(B["T"] / np.median(B["T"][ref]) - 1) > 0.15) | \
          (np.abs(B["amp_mm"] / np.median(B["amp_mm"][ref]) - 1) > 0.15)
    endur = float(tt[-1])
    broke = False
    for i in range(len(dev) - 1):
        if dev[i] and dev[i + 1]:
            endur = float(B["t0"][i])
            broke = True
            break

    # Downsample waveform for visualization (~10 Hz, max ~6000 points)
    step = max(1, len(tt) // 6000, int(FS_W / 10))
    t_down = [float(round(val, 2)) for val in tt[::step]]
    vol_down = [float(round(val, 1)) for val in vol_ml[::step]]
    flow_down = [float(round(val, 1)) for val in flow[::step]]

    return {
        "n_breaths": n_breaths,
        "ratio": {
            "inhale_s": round(tin0, 2),
            "hold_s": round(th0, 2),
            "exhale_s": round(tex0, 2),
            "hold_ratio": round(th0 / tin0, 2) if tin0 > 0 else 0.0,
            "exhale_ratio": round(tex0 / tin0, 2) if tin0 > 0 else 0.0,
            "normalized": f"1 : {th0 / tin0:.2f} : {tex0 / tin0:.2f}" if tin0 > 0 else "N/A",
        },
        "bpm": round(bpm, 1),
        "tidal_volume_ml": round(tv0, 1),
        "peak_expiratory_flow_ml_s": round(pef0, 1),
        "mean_expiratory_flow_ml_s": round(mef0, 1),
        "endurance_s": round(endur, 1),
        "never_broke": bool(not broke),
        "waveform": {
            "t": t_down,
            "volume_ml": vol_down,
            "flow_ml_s": flow_down,
        },
        "breath_details": breaths,
        "assumptions": assumptions,
        "warnings": warnings_list,
    }


def analyze_ltspice_export(
    text_or_path: str | bytes,
    time_scale: float | None = None,
) -> dict[str, Any]:
    """
    Parse an LTspice text export of V(out) vs time and recover breathing parameters.
    text_or_path: file path, raw bytes, or text string.
    time_scale: simulation acceleration factor (default 100, or from pwl_meta.json).
    """
    if time_scale is None:
        time_scale = 100.0
        if isinstance(text_or_path, str) and os.path.isfile(text_or_path):
            meta_path = os.path.join(os.path.dirname(os.path.abspath(text_or_path)), "pwl_meta.json")
            if os.path.exists(meta_path):
                try:
                    with open(meta_path, "r", encoding="utf-8") as mf:
                        meta = json.load(mf)
                        time_scale = float(meta.get("time_scale", 100.0))
                except Exception:
                    pass

    # Read data efficiently
    ts: list[float] = []
    vs: list[float] = []

    if isinstance(text_or_path, bytes):
        stream = io.BytesIO(text_or_path)
    elif isinstance(text_or_path, str) and os.path.isfile(text_or_path):
        stream = open(text_or_path, "rb")
    elif isinstance(text_or_path, str):
        stream = io.BytesIO(text_or_path.encode("utf-8", errors="ignore"))
    else:
        raise ValueError("text_or_path must be a file path, string, or bytes")

    try:
        for raw_line in stream:
            line = raw_line.strip()
            if not line:
                continue
            c0 = line[:1]
            if not (c0.isdigit() or c0 in b"-+"):
                continue  # skip headers, Step Information, etc.
            parts = line.split()
            try:
                a, b = float(parts[0]), float(parts[1])
            except (ValueError, IndexError):
                continue
            if ts and a < ts[-1]:
                break  # keep first .step run only
            ts.append(a)
            vs.append(b)
    finally:
        if isinstance(text_or_path, str) and os.path.isfile(text_or_path) and hasattr(stream, "close"):
            stream.close()

    if len(ts) < 20:
        raise ValueError("Too few valid data points found in LTspice export (minimum 20 required)")

    ts_arr = np.array(ts)
    vs_arr = np.array(vs)

    # Filter out duplicate time stamps
    keep = np.concatenate(([True], np.diff(ts_arr) > 0))
    ts_arr = ts_arr[keep]
    vs_arr = vs_arr[keep]

    # Demodulate carrier amplitude
    FS = 400e3  # 400 kHz uniform grid (40 points per 10 kHz carrier cycle)
    tu = np.arange(ts_arr[0], ts_arr[-1], 1 / FS)
    vu = CubicSpline(ts_arr, vs_arr)(tu)

    blk = int(FS / F_CARRIER * 10)  # 10 carrier cycles (1 ms simulation time)
    n = vu.size // blk
    if n < 10:
        raise ValueError("Simulation duration is too short for carrier demodulation")

    vb = vu[: n * blk].reshape(n, blk)
    env = np.sqrt(2.0 * vb.var(axis=1))  # sine amplitude = sqrt(2 * variance)
    t_env = ts_arr[0] + (np.arange(n) + 0.5) * blk / FS
    fs_env = FS / blk  # 1000 Hz

    cut = 2.0 * time_scale
    if cut >= fs_env / 2.0:
        raise ValueError(
            f"TIME_SCALE ({time_scale}) is too large for this demodulator "
            f"(effective cutoff {cut:.1f} Hz exceeds Nyquist {fs_env / 2:.1f} Hz)"
        )

    env = sosfiltfilt(butter(2, cut, fs=fs_env, output="sos"), env)

    # Recover capacitance -> overlap -> belt expansion
    # Vout = Vin / sqrt(1 + (omega*R*C)^2)  =>  C = sqrt((Vin/env)^2 - 1) / (omega*R)
    ratio_sq = np.maximum(1.0, (VIN / env) ** 2)
    C_rec = np.sqrt(ratio_sq - 1.0) / (OMEGA * R)
    ov = C_rec * GAP / (EPS0 * EPS_R * WIDTH)
    exp_env = (OVERLAP0 - ov) * 1e3 / ETA  # mm

    t_real_env = (t_env - t_env[0]) * time_scale
    FS_W = 100.0  # 100 Hz real-time grid
    tt = np.arange(0, t_real_env[-1], 1 / FS_W)
    exp_rec = np.interp(tt, t_real_env, exp_env)
    vol_ml = exp_rec * K_ML_PER_MM
    flow = savgol_filter(vol_ml, 51, 3, deriv=1, delta=1 / FS_W)

    res = _extract_parameters(
        tt, exp_rec, vol_ml, flow,
        warnings=[f"Demodulated from LTspice carrier with time_scale = {time_scale}"],
        extra_assumptions=[f"Simulation time-scale factor: {time_scale}x real time"],
    )
    res["carrier_voltage_peak_range_v"] = [round(float(env.min()), 3), round(float(env.max()), 3)]
    res["capacitance_range_pf"] = [round(float(C_rec.min() * 1e12), 2), round(float(C_rec.max() * 1e12), 2)]
    return res


def analyze_wave_csv(path_or_bytes: str | bytes) -> dict[str, Any]:
    """
    Analyze recovered breathing wave CSV with columns time_s,expansion_mm,volume_mL,flow_mL_s.
    Trims the first and last 1 s before computing parameters to remove edge artifacts.
    """
    if isinstance(path_or_bytes, bytes):
        bio = io.BytesIO(path_or_bytes)
        d = np.loadtxt(bio, delimiter=",", skiprows=1)
    elif isinstance(path_or_bytes, str) and os.path.isfile(path_or_bytes):
        d = np.loadtxt(path_or_bytes, delimiter=",", skiprows=1)
    elif isinstance(path_or_bytes, str):
        bio = io.StringIO(path_or_bytes)
        d = np.loadtxt(bio, delimiter=",", skiprows=1)
    else:
        raise ValueError("path_or_bytes must be a file path, string, or bytes")

    if d.ndim != 2 or d.shape[1] < 2:
        raise ValueError("CSV must contain at least time_s and expansion_mm columns")

    tt_full = d[:, 0]
    exp_full = d[:, 1]
    vol_full = d[:, 2] if d.shape[1] >= 3 else exp_full * K_ML_PER_MM
    flow_full = d[:, 3] if d.shape[1] >= 4 else savgol_filter(vol_full, 51, 3, deriv=1, delta=0.01)

    warnings: list[str] = []
    # Trim first and last 1.0 s as required
    if tt_full[-1] - tt_full[0] > 4.0:
        mask = (tt_full >= tt_full[0] + 1.0) & (tt_full <= tt_full[-1] - 1.0)
        tt = tt_full[mask]
        exp_rec = exp_full[mask]
        vol_ml = vol_full[mask]
        flow = flow_full[mask]
        warnings.append("Trimmed first and last 1.0 s of recording to remove filter edge effects.")
    else:
        tt = tt_full
        exp_rec = exp_full
        vol_ml = vol_full
        flow = flow_full

    return _extract_parameters(tt, exp_rec, vol_ml, flow, warnings=warnings)


# ==================================
# CLI Execution
# ==================================
def main():
    import argparse

    parser = argparse.ArgumentParser(description="Analyze capacitive breath sensor LTspice output or wave CSV.")
    parser.add_argument("input_file", nargs="?", default="ltspice_export.txt", help="Path to LTspice export or wave CSV")
    parser.add_argument("--time-scale", type=float, default=None, help="LTspice time acceleration factor")
    parser.add_argument("--save-png", type=str, default=None, help="Save summary plot PNG")
    args = parser.parse_args()

    target = args.input_file
    if not os.path.exists(target):
        print(f"Error: file not found: {target}")
        sys.exit(1)

    if target.lower().endswith(".csv"):
        res = analyze_wave_csv(target)
    else:
        res = analyze_ltspice_export(target, time_scale=args.time_scale)

    print("=" * 56)
    print(" BREATH SENSOR - RESULTS (Smart Pranayama Breathing Coach)")
    print("=" * 56)
    print(f"Breaths detected      : {res['n_breaths']}")
    r = res["ratio"]
    print(f"1. Inhale:Hold:Exhale = {r['inhale_s']:.1f}s : {r['hold_s']:.1f}s : {r['exhale_s']:.1f}s  (= {r['normalized']})")
    print(f"2. Breaths per minute = {res['bpm']:.1f}")
    print(f"3. Tidal volume        = {res['tidal_volume_ml']:.0f} mL  ({res['assumptions'][0]})")
    print(f"4. Peak expiratory flow= {res['peak_expiratory_flow_ml_s']:.0f} mL/s ; mean expiratory flow = {res['mean_expiratory_flow_ml_s']:.0f} mL/s")
    endur_str = f"{res['endurance_s']:.0f} s"
    if res["never_broke"]:
        endur_str += "  (never broke within this recording)"
    print(f"5. Pattern sustained   = {endur_str}")

    if res.get("warnings"):
        print("\nWarnings:")
        for w in res["warnings"]:
            print(f"  * {w}")

    if args.save_png:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        wf = res["waveform"]
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
        ax1.plot(wf["t"], wf["volume_ml"], color="#557448", label="Recovered Volume (mL)")
        ax1.set_ylabel("Volume (mL)")
        ax1.legend(loc="upper right")
        ax1.grid(True)
        ax1.set_title(f"Breath Sensor Recovery (BPM: {res['bpm']}, Tidal Vol: {res['tidal_volume_ml']} mL)")

        ax2.plot(wf["t"], wf["flow_ml_s"], color="#8c3935", label="Flow (mL/s)")
        ax2.set_xlabel("Time (s)")
        ax2.set_ylabel("Flow (mL/s)")
        ax2.legend(loc="upper right")
        ax2.grid(True)

        plt.tight_layout()
        plt.savefig(args.save_png, dpi=120)
        print(f"\nPlot saved to {args.save_png}")


if __name__ == "__main__":
    main()
