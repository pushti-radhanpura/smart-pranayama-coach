"""
sensor_service.py
=================
Glue service between FastAPI endpoints and the sensor_pipeline analysis module.
Caches bundled sample analysis and downsamples wave data for browser replay.
"""
from __future__ import annotations

import os
import sys
from typing import Any

# Ensure sensor_pipeline is on sys.path
HERE = os.path.dirname(os.path.abspath(__file__))
SENSOR_PIPELINE_DIR = os.path.abspath(os.path.join(HERE, "..", "sensor_pipeline"))
if SENSOR_PIPELINE_DIR not in sys.path:
    sys.path.insert(0, SENSOR_PIPELINE_DIR)

import analyze_ltspice

_cached_sample: dict[str, Any] | None = None
_cached_wave: dict[str, list[float]] | None = None


def analyze_upload(
    content: bytes,
    filename: str,
    time_scale: float = 100.0,
) -> dict[str, Any]:
    """Analyze an uploaded LTspice export (.txt) or recovered wave (.csv)."""
    fn_lower = filename.lower()
    if fn_lower.endswith(".csv"):
        return analyze_ltspice.analyze_wave_csv(content)
    # Default to LTspice export demodulation (.txt, etc.)
    return analyze_ltspice.analyze_ltspice_export(content, time_scale=time_scale)


def bundled_sample() -> dict[str, Any]:
    """Return analysis of the bundled breath_wave_from_ltspice.csv, cached in memory."""
    global _cached_sample
    if _cached_sample is None:
        csv_path = os.path.join(SENSOR_PIPELINE_DIR, "breath_wave_from_ltspice.csv")
        if not os.path.exists(csv_path):
            raise FileNotFoundError(f"Bundled sample wave not found at {csv_path}")
        _cached_sample = analyze_ltspice.analyze_wave_csv(csv_path)
    return _cached_sample


def get_sensor_wave() -> dict[str, list[float]]:
    """
    Return the bundled wave downsampled to ~10 Hz (t, volume) for live browser replay
    into the Pranayama Coach client.
    """
    global _cached_wave
    if _cached_wave is None:
        csv_path = os.path.join(SENSOR_PIPELINE_DIR, "breath_wave_from_ltspice.csv")
        if not os.path.exists(csv_path):
            raise FileNotFoundError(f"Bundled sample wave not found at {csv_path}")
        import numpy as np

        data = np.loadtxt(csv_path, delimiter=",", skiprows=1)
        tt = data[:, 0]
        # column 2 is volume_mL, or column 1 (expansion_mm) * 50
        vol = data[:, 2] if data.shape[1] >= 3 else data[:, 1] * 50.0

        # Downsample to ~10 Hz (data is at 100 Hz, so step 10 = 0.1 s = 10 Hz)
        step = 10
        t_10hz = [round(float(v), 2) for v in tt[::step]]
        vol_10hz = [round(float(v), 2) for v in vol[::step]]
        _cached_wave = {"t": t_10hz, "volume": vol_10hz}
    return _cached_wave
