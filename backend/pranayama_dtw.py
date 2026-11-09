"""
pranayama_dtw.py
================
Compare a user's multi-cycle breathing signal (e.g. 5 minutes) against the ideal
pattern of a pranayama type using Sakoe-Chiba-constrained DTW.

Pipeline
--------
1. preprocess : resample -> low-pass -> remove baseline drift -> z-normalise
2. template   : build the ideal waveform from phase timings (inhale/hold/exhale/hold)
3. segment    : cut the user's signal into individual breath cycles
4. per cycle  : banded DTW (shape) + tempo ratio + per-phase timing feedback
5. session    : banded DTW of the WHOLE 5 min against the tiled ideal signal
6. plot       : dashboard PNG

Run the demo (synthetic user):
    python pranayama_dtw.py --pattern box
    python pranayama_dtw.py --pattern anulom_1_4_2 --duration 300 --save out.png

Use real data (CSV with two columns: time_seconds,value):
    python pranayama_dtw.py --pattern box --csv my_breath.csv

Dependencies: numpy, scipy, matplotlib   (numba optional: makes DTW ~100x faster)
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from dataclasses import dataclass

import numpy as np
from scipy.signal import butter, sosfiltfilt

try:  # optional speed-up
    from numba import njit
except ImportError:  # pure-python fallback, still fine thanks to the narrow band
    def njit(*a, **k):
        if len(a) == 1 and callable(a[0]):
            return a[0]
        return lambda f: f


# ----------------------------------------------------------------------------
# 1. Ideal patterns
# ----------------------------------------------------------------------------
@dataclass(frozen=True)
class Pattern:
    name: str
    inhale: float      # seconds
    hold_in: float
    exhale: float
    hold_out: float
    cue: str = ""              # one-line tip the coach can speak
    eyes_closed: bool = False  # True -> voice feedback is essential
    key: str = ""
    curve_file: str = ""       # optional: your teammate's ideal curve (csv/txt/npy)
    curve_fs: float = 0.0      # samples/second - only needed for ONE-column files
    cycles_in_file: int = 1    # how many breath cycles the file contains
    curve_s: float = 0.0       # (filled automatically) duration of one cycle in the file
    invert: bool = False       # True if your curve goes DOWN while inhaling
    curve_type: str = "volume"  # what your LIVE breath signal is: "volume" (chest/belly position,
                               # goes up while inhaling) or "flow" (+ inhale / - exhale)

    def __post_init__(self):
        if min(self.inhale, self.hold_in, self.exhale, self.hold_out) < 0:
            raise ValueError(f"{self.name}: durations cannot be negative")
        if self.inhale <= 0 or self.exhale <= 0:
            raise ValueError(f"{self.name}: inhale and exhale must be greater than 0")

    @property
    def cycle_s(self) -> float:
        if self.curve_s > 0:
            return self.curve_s
        return self.inhale + self.hold_in + self.exhale + self.hold_out


# >>> THE IDEAL BREATH TYPES LIVE IN patterns.json (same folder as this file). <<<
# Each entry is either built from timings (inhale/hold_in/exhale/hold_out) or
# from a curve file your teammate made ("curve_file": "curves/box.csv").
_CURVES: dict = {}     # key -> (normalised one-cycle array, seconds per cycle)


def _rotate_to_inhale_start(c: np.ndarray) -> np.ndarray:
    """Rotate a one-cycle curve so index 0 is where the inhale begins (bottom -> rising)."""
    n = len(c)
    above = c >= 0.5
    ups = np.where(~np.roll(above, 1) & above)[0]
    if len(ups) == 0:
        return c
    k, steps = int(ups[0]), 0
    while c[(k - 1) % n] > 0.1 and steps < n:
        k, steps = (k - 1) % n, steps + 1
    return np.roll(c, -k)


def _read_text(path: str) -> str:
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-16", "cp1252"):      # Excel often saves cp1252 / utf-16
        try:
            return raw.decode(enc)
        except UnicodeError:
            pass
    return raw.decode("latin-1", errors="replace")


def _parse_py(path: str):
    """Read a wave stored INSIDE a Python file, e.g.  wave = [0.0, 0.1, ...]  or
    y = np.array([...]).  The file is only READ, never run, so it is safe.
    Waves that the script COMPUTES (np.sin(...), loops, functions) cannot be read this way."""
    import ast
    name = os.path.basename(path)
    try:
        tree = ast.parse(_read_text(path))
    except SyntaxError as e:
        raise ValueError(f"this Python file has a syntax error at line {e.lineno}")

    def numbers(node):
        try:
            v = ast.literal_eval(node)
        except Exception:
            v = None
        if isinstance(v, (list, tuple)) and len(v) >= 20 and all(
                isinstance(x, (int, float)) and not isinstance(x, bool) for x in v):
            return [float(x) for x in v]
        if isinstance(node, ast.Call) and node.args:           # np.array([...]), list([...])
            return numbers(node.args[0])
        return None

    # Generator-style script:  SEGS = [('in', 2.5, 'sine'), ('hold', 4, 'sine'), ('out', 3, 'sine')]
    segs, title = None, None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                val = ast.literal_eval(node.value)
            except Exception:
                continue
            nm = node.targets[0].id
            if nm == "SEGS" and isinstance(val, (list, tuple)) and val and all(
                    isinstance(x, (list, tuple)) and len(x) >= 2 and x[0] in ("in", "out", "hold")
                    and isinstance(x[1], (int, float)) for x in val):
                segs = [(x[0], float(x[1]), x[2] if len(x) > 2 else "sine") for x in val]
            elif nm == "TITLE" and isinstance(val, str):
                title = val
            elif nm == "NAME" and isinstance(val, str) and title is None:
                title = val
    if segs:
        return {"segs": segs, "title": title}

    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        vals = numbers(value)
        if vals and isinstance(targets[0], ast.Name):
            found.append((targets[0].id, np.array(vals)))
    if not found:
        raise ValueError(
            "this Python file does not contain the wave as a plain list of numbers (it probably "
            "calculates it with code). Ask your friend to export it: after the wave is computed, "
            "add   np.savetxt('wave.csv', np.column_stack([t, wave]), delimiter=',')   "
            "and use the CSV file. Or send me the file and I will read it")
    # a time list (strictly increasing) next to a value list of the same length?
    for n1, a in found:
        for n2, b in found:
            if n1 != n2 and len(a) == len(b) and np.all(np.diff(a) > 0) and not np.all(np.diff(b) > 0):
                print(f"  note: {name}: using '{n1}' as time and '{n2}' as the breath wave")
                return a, b
    n, y = max(found, key=lambda kv: len(kv[1]))
    print(f"  note: {name}: using the list called '{n}' ({len(y)} numbers) as the breath wave"
          + (f" - other lists ignored: {[k for k, _ in found if k != n]}" if len(found) > 1 else ""))
    return None, y


def _parse_table(path: str):
    """Find the numbers in a messy file. Ignores titles, header lines, text columns and
    rows with blank cells. Returns (time_or_None, values)."""
    name = os.path.basename(path)
    low = path.lower()
    if low.endswith((".xlsx", ".xls")):
        raise ValueError("this is an Excel file. Open it in Excel, choose File > Save As > "
                         "CSV (Comma delimited), and use the .csv file instead")
    if low.endswith(".py"):
        return _parse_py(path)
    if low.endswith(".npy"):
        d = np.asarray(np.load(path), float)
        return (None, d.ravel()) if d.ndim == 1 or d.shape[1] == 1 else (d[:, 0], d[:, 1])
    lines = [ln.strip() for ln in _read_text(path).splitlines() if ln.strip()]
    if not lines:
        raise ValueError("the file is empty")
    counts = {dl: sum(dl in ln for ln in lines) for dl in (",", "\t", ";")}
    best = max(counts, key=counts.get)
    delim = best if counts[best] >= 0.5 * len(lines) else None

    def num(tok):
        tok = tok.strip().strip('"')
        if delim != ",":
            tok = tok.replace(",", ".")                 # decimal comma, e.g. 0,35
        try:
            v = float(tok)
        except ValueError:
            return None
        return v if np.isfinite(v) else None

    rows = [[num(c) for c in (ln.split(delim) if delim else ln.split())] for ln in lines]
    ncols = max(len(r) for r in rows)
    n_num = [sum(1 for r in rows if c < len(r) and r[c] is not None) for c in range(ncols)]
    if max(n_num) == 0:
        raise ValueError("no numbers found in the file")
    good = [c for c in range(ncols) if n_num[c] >= 0.8 * max(n_num) and n_num[c] > 0]
    use = good[:2]
    data = [[r[c] for c in use] for r in rows if all(c < len(r) and r[c] is not None for c in use)]
    skipped = len(rows) - len(data)
    if skipped:
        print(f"  note: {name}: ignored {skipped} line(s) that were not numbers (titles, headers, blank cells)")
    if len(good) > 2:
        print(f"  note: {name}: has {len(good)} numeric columns - using the first two "
              f"(time, value). Tell me if that is wrong.")
    d = np.array(data, float)
    return (None, d[:, 0]) if len(use) == 1 else (d[:, 0], d[:, 1])


def _count_cycles(y: np.ndarray) -> int:
    c = (y - y.min()) / (y.max() - y.min() + 1e-12)
    armed, n = False, 0
    for v in c:
        if v < 0.3:
            armed = True
        elif v > 0.7 and armed:
            n, armed = n + 1, False
    return n


def _flow_cycle(segs, fs: float = 100.0):
    """Rebuild ONE breath cycle from a SEGS recipe using the same formulas as the generator
    scripts (sine / plateau shaped flow, equal volume in and out). Returns (flow, exact timings)."""
    k = next((i for i, sg in enumerate(segs) if sg[0] == "in"), None)
    if k is None:
        raise ValueError("SEGS has no 'in' (inhale) part")
    segs = segs[k:] + segs[:k]                      # start at the beginning of an inhale
    parts, n_in, n_out = [], 0, 0
    for ph, sec, shape in segs:
        n = max(int(round(sec * fs)), 1)
        if ph == "hold":
            parts.append(("hold", np.zeros(n)))
            continue
        x = (np.arange(n) + 0.5) / n
        if shape == "plateau":
            sh = np.sin(0.5 * np.pi * np.clip(np.minimum(x, 1 - x) / 0.15, 0, 1))
        else:
            sh = np.sin(np.pi * x)
        sh = sh / (sh.sum() / fs)                   # area under the curve = 1 (volume)
        parts.append((ph, sh))
        n_in += ph == "in"
        n_out += ph == "out"
    if n_out == 0:
        raise ValueError("SEGS has no 'out' (exhale) part")
    flow = np.concatenate([p if ph == "in" else (-p * n_in / n_out if ph == "out" else p)
                           for ph, p in parts])     # balanced, so volume returns to its start
    exact = {"inhale": 0.0, "hold_in": 0.0, "exhale": 0.0, "hold_out": 0.0}
    last = None
    for ph, sec, _ in segs:
        if ph == "in":
            exact["inhale"] += sec
        elif ph == "out":
            exact["exhale"] += sec
        else:
            exact["hold_in" if last == "in" else "hold_out"] += sec
        if ph != "hold":
            last = ph
    return flow, exact


def _read_curve(path: str, fs: float, cycles: int, invert: bool = False, curve_type: str = "volume"):
    """Reads .csv / .txt / .tsv / .npy (Excel: save as CSV first). Accepts 1 column (values)
    or 2+ columns (time_seconds, value). Titles / headers / text columns are ignored."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"file not found: {path}. Put it in the 'curves' folder and make "
                                f"sure the name in patterns.json matches letter for letter")
    name = os.path.basename(path)
    res = _parse_table(path)
    if isinstance(res, dict):                              # generator script with a SEGS recipe
        flow, exact = _flow_cycle(res["segs"])
        dur = float(sum(sec for _, sec, _ in res["segs"]))
        y = flow if curve_type == "flow" else np.r_[0.0, np.cumsum(flow)[:-1]] / 100.0
        arr = (y - y.min()) / (y.max() - y.min() + 1e-12)
        if invert:
            arr = 1 - arr
        print(f"  note: {name}: read the recipe SEGS (in {exact['inhale']:g}s, hold {exact['hold_in']:g}s, "
              f"out {exact['exhale']:g}s, pause {exact['hold_out']:g}s) and rebuilt the "
              f"{'airflow' if curve_type == 'flow' else 'volume'} wave. The script was NOT run.")
        exact["title"] = res.get("title")
        return arr, dur, exact
    t, y = res
    if len(y) < 20:
        raise ValueError(f"only {len(y)} numbers found - a curve needs at least 20 samples")
    if t is None:
        if not fs:
            raise ValueError("the file has one column of values, so patterns.json must say how "
                             "many samples per second it has, e.g.  \"curve_fs\": 10")
        total = len(y) / fs
    else:
        if np.mean(np.diff(t) > 0) < 0.95:
            raise ValueError("the first column does not look like time (it does not keep "
                             "increasing). If the file has only breath values, keep one column")
        order = np.argsort(t, kind="stable")
        t, y = t[order], y[order]
        keep = np.r_[True, np.diff(t) > 0]
        t, y = t[keep], y[keep]
        dt = float(np.median(np.diff(t)))
        if dt >= 1:                                            # looks like milliseconds
            print(f"  note: {name}: time column looks like milliseconds -> converting")
            t, dt = t / 1000.0, dt / 1000.0
        total = float(t[-1] - t[0]) + dt
        n = max(20, int(round(total / dt)))
        y = np.interp(t[0] + np.arange(n) * dt, t, y)          # even spacing
    if invert:
        y = -y
    if y.max() - y.min() < 1e-9 * max(1.0, float(np.abs(y).mean())):
        raise ValueError("the curve is completely flat (every number is the same)")
    k = max(1, int(cycles))
    n_up = _count_cycles(y)
    if n_up >= 2 and k == 1:
        print(f"  WARNING {name}: this file seems to contain about {n_up} breath cycles but "
              f"\"cycles_in_file\" is 1. Add  \"cycles_in_file\": {n_up}  to its block in patterns.json")
    m = len(y) // k
    parts = y[: m * k].reshape(k, m)
    parts = np.array([(q - q.min()) / (q.max() - q.min() + 1e-12) for q in parts])
    arr = parts.mean(0)
    arr = (arr - arr.min()) / (arr.max() - arr.min() + 1e-12)
    dur = total / k
    arr = _rotate_to_inhale_start(arr)                     # rough start
    # precise start = mid-inhale crossing minus half the inhale (how generated curves are built)
    est = _estimate_phases(arr, dur)
    mid = int(np.argmax(arr >= 0.5))
    shift = mid - int(round(est["inhale"] / 2 / dur * len(arr)))
    return np.roll(arr, -shift), dur, None


def _estimate_phases(c: np.ndarray, dur: float) -> dict:
    """Estimate inhale / hold / exhale / hold durations from a curve (used only if the
    json doesn't give them; they feed the spoken tips, not the DTW comparison)."""
    fs_c = len(c) / dur
    mid = int(np.argmax(c >= 0.5))
    m = _phase_measures(np.roll(c, -mid), fs_c)
    if not m:
        return {"inhale": dur * 0.4, "hold_in": 0.0, "exhale": dur * 0.6, "hold_out": 0.0}
    inh = m["inhale"] * 0.7 / 0.52
    exh = m["exhale"] * 0.7 / 0.52
    h1 = max(0.0, m["hold_in"] - 0.24 * (inh + exh))
    h2 = max(0.0, m["hold_out"] - 0.24 * (inh + exh))
    tiny = max(0.4, 0.04 * dur)
    h1, h2 = (0.0 if h1 < tiny else h1), (0.0 if h2 < tiny else h2)
    k = dur / (inh + h1 + exh + h2)
    return {"inhale": inh * k, "hold_in": h1 * k, "exhale": exh * k, "hold_out": h2 * k}


LOAD_PROBLEMS: list = []     # human-readable list of entries that could not be loaded


def load_patterns(path: str | None = None) -> dict:
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "patterns.json")
    base = os.path.dirname(os.path.abspath(path))
    LOAD_PROBLEMS.clear()
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
    except FileNotFoundError:
        raise SystemExit(f"\nCannot find {path}\nIt must be in the same folder as the .py files.")
    except json.JSONDecodeError as e:
        raise SystemExit(f"\npatterns.json has a typing mistake at line {e.lineno}, column {e.colno}: {e.msg}\n"
                         f"Look at that line and the line just above it: a missing comma, a missing quote, "
                         f"a curly quote, or an extra comma at the end of a block.")
    out = {}
    for k, v in data.items():
        if k.startswith("_"):            # "_readme" style notes are ignored
            continue
        try:
            if not isinstance(v, dict):
                raise ValueError("this entry must be a { ... } block")
            v = dict(v)
            if v.get("curve_file"):
                arr, dur, exact = _read_curve(os.path.join(base, v["curve_file"]), v.get("curve_fs", 0),
                                              v.get("cycles_in_file", 1), bool(v.get("invert", False)),
                                              v.get("curve_type", "volume"))
                _CURVES[k] = (arr, dur)
                est = dict(exact) if exact else _estimate_phases(arr, dur)
                title = est.pop("title", None)
                if title:
                    v.setdefault("name", title)
                given = [f for f in ("inhale", "hold_in", "exhale", "hold_out") if f in v]
                for f_, val in est.items():
                    v.setdefault(f_, round(val, 2))
                if len(given) == 4:
                    tot = sum(v[f_] for f_ in given)
                    if abs(tot - dur) / dur > 0.10:
                        print(f"  WARNING {k}: timings in patterns.json add up to {tot:.1f}s but the "
                              f"curve file's cycle lasts {dur:.1f}s")
                v["curve_s"] = float(dur)
            out[k] = Pattern(key=k, **v)
        except Exception as e:                       # skip this one, keep the others
            _CURVES.pop(k, None)
            msg = f"'{k}' was skipped: {e}"
            LOAD_PROBLEMS.append(msg)
            print(f"  PROBLEM {msg}")
    if LOAD_PROBLEMS:
        print(f"  -> only {len(out)} of {len(out) + len(LOAD_PROBLEMS)} patterns could be loaded; "
              f"the rest were skipped (see the PROBLEM lines above)")
    return out


def _smooth_ramp(duration: float, a: float, b: float, fs: float) -> np.ndarray:
    n = max(2, int(round(duration * fs)))
    x = np.linspace(0, 1, n, endpoint=False)
    return a + (b - a) * (x * x * (3 - 2 * x))        # smoothstep


def _flat(duration: float, v: float, fs: float) -> np.ndarray:
    return np.full(int(round(duration * fs)), float(v))


def one_cycle(p: Pattern, fs: float) -> np.ndarray:
    """Un-rolled cycle: starts at the beginning of the inhale."""
    if p.key in _CURVES:                                   # curve supplied by a file
        arr, dur = _CURVES[p.key]
        n = max(2, int(round(dur * fs)))
        grid = np.linspace(0, dur, n, endpoint=False)
        return np.interp(grid, np.arange(len(arr)) * dur / len(arr), arr, period=dur)
    return np.concatenate([
        _smooth_ramp(p.inhale, 0, 1, fs),
        _flat(p.hold_in, 1, fs),
        _smooth_ramp(p.exhale, 1, 0, fs),
        _flat(p.hold_out, 0, fs),
    ])


def tiled_template(p: Pattern, fs: float, n_samples: int) -> np.ndarray:
    c = one_cycle(p, fs)
    reps = int(np.ceil(n_samples / len(c)))
    return np.tile(c, reps)[:n_samples]


def aligned_cycle(p: Pattern, fs: float) -> tuple[np.ndarray, dict]:
    """Ideal cycle rolled to start at MID-inhale (matches how we segment the user)
    plus the phase boundaries (in seconds) inside that rolled cycle."""
    c = one_cycle(p, fs)
    shift = int(np.argmax(c >= 0.5))                       # mid-inhale
    rolled = np.roll(c, -shift)
    b = {
        "inhale_end": p.inhale / 2,
        "hold_in_end": p.inhale / 2 + p.hold_in,
        "exhale_end": p.inhale / 2 + p.hold_in + p.exhale,
        "cycle_end": len(rolled) / fs,
    }
    return rolled, b


# ----------------------------------------------------------------------------
# 2. Pre-processing  (raw breath signal  ->  clean comparable curve)
# ----------------------------------------------------------------------------
def znorm(x: np.ndarray) -> np.ndarray:
    s = x.std()
    return (x - x.mean()) / (s if s > 1e-9 else 1.0)


def preprocess(t: np.ndarray, raw: np.ndarray, fs: float = 10.0,
               lowpass_hz: float = 2.5, baseline_hz: float = 0.01) -> np.ndarray:
    """Irregular (t, raw) -> uniformly sampled, smooth, drift-free, z-scored."""
    grid = np.arange(t[0], t[-1], 1 / fs)
    x = np.interp(grid, t, raw)
    x = sosfiltfilt(butter(4, lowpass_hz, "low", fs=fs, output="sos"), x)      # denoise
    x = sosfiltfilt(butter(2, baseline_hz, "high", fs=fs, output="sos"), x)    # drift
    return znorm(x)


# ----------------------------------------------------------------------------
# 3. Constrained DTW (Sakoe-Chiba band)
# ----------------------------------------------------------------------------
@njit(cache=True)
def _dtw_matrix(x, y, radius):
    """Accumulated-cost matrix, only cells inside the band are filled.
    The band follows the line j = i*(m-1)/(n-1), so unequal lengths are OK."""
    n, m = len(x), len(y)
    D = np.full((n + 1, m + 1), np.inf)
    D[0, 0] = 0.0
    slope = (m - 1) / max(n - 1, 1)
    for i in range(1, n + 1):
        c = (i - 1) * slope
        lo = max(1, int(np.floor(c - radius)) + 1)
        hi = min(m, int(np.ceil(c + radius)) + 1)
        for j in range(lo, hi + 1):
            cost = abs(x[i - 1] - y[j - 1])
            best = min(D[i - 1, j], D[i, j - 1], D[i - 1, j - 1])
            D[i, j] = cost + best
    return D


@njit(cache=True)
def _traceback(D):
    i, j = D.shape[0] - 1, D.shape[1] - 1
    pi = np.empty(i + j, dtype=np.int64)
    pj = np.empty(i + j, dtype=np.int64)
    k = 0
    pi[k], pj[k] = i - 1, j - 1
    k += 1
    while i > 1 or j > 1:
        a, b, c = D[i - 1, j - 1], D[i - 1, j], D[i, j - 1]
        if a <= b and a <= c:
            i -= 1
            j -= 1
        elif b < c:
            i -= 1
        else:
            j -= 1
        pi[k], pj[k] = i - 1, j - 1
        k += 1
    return pi[:k][::-1], pj[:k][::-1]


def dtw_banded(x: np.ndarray, y: np.ndarray, radius: int):
    """Returns (path-normalised distance, path_x, path_y, band-limited D)."""
    x = np.ascontiguousarray(x, dtype=np.float64)
    y = np.ascontiguousarray(y, dtype=np.float64)
    # band must be wide enough to physically reach the corner
    r = max(int(radius), 1, int(np.ceil(len(y) / len(x))), int(np.ceil(len(x) / len(y))))
    D = _dtw_matrix(x, y, r)
    if not np.isfinite(D[-1, -1]):
        raise ValueError("Band too narrow for these lengths")
    pi, pj = _traceback(D)
    return D[-1, -1] / len(pi), pi, pj, D


# ----------------------------------------------------------------------------
# 4. Segment the user's signal into breath cycles
# ----------------------------------------------------------------------------
def segment_cycles(x: np.ndarray, fs: float, min_cycle_s: float) -> list[tuple[int, int]]:
    """Cycle boundary = upward crossing of the mid-level (i.e. mid-inhale).
    The signal is first normalised against a ROLLING min/max (about 1.2 cycles wide) so
    that shallow and deep breaths are both detected, then hysteresis avoids false cycles
    from noise or holds."""
    from scipy.ndimage import maximum_filter1d, minimum_filter1d, uniform_filter1d
    w = max(5, int(2.4 * min_cycle_s * fs)) | 1            # ~1.2 x the ideal cycle length
    lo_r = uniform_filter1d(minimum_filter1d(x, w), w)
    hi_r = uniform_filter1d(maximum_filter1d(x, w), w)
    rng = hi_r - lo_r
    rng = np.maximum(rng, 0.25 * np.median(rng) + 1e-9)    # never amplify pure noise too much
    xn = (x - lo_r) / rng                                   # ~0 (bottom) .. ~1 (top)
    mid, h = 0.5, 0.15
    armed, starts = False, []
    for i, v in enumerate(xn):
        if v < mid - h:
            armed = True
        elif v > mid + h and armed:
            k = i
            while k > 0 and xn[k] >= mid:                  # back-track to the actual mid crossing
                k -= 1
            if not starts or k - starts[-1] >= min_cycle_s * fs:
                starts.append(k)
            armed = False
    return list(zip(starts[:-1], starts[1:]))


# ----------------------------------------------------------------------------
# 5. Per-cycle analysis
# ----------------------------------------------------------------------------
@dataclass
class CycleResult:
    index: int
    start_s: float
    duration_s: float
    shape_dist: float
    shape_score: float
    tempo_ratio: float
    tempo_score: float
    score: float
    phases: dict            # estimated durations (s): ideal vs user
    path: tuple             # (path_user, path_ideal) on the resampled cycle


def _phase_measures(cyc: np.ndarray, fs: float, hi_t=0.85, lo_t=0.15):
    """Measure phase lengths straight from threshold crossings (robust on plateaus,
    where a DTW path is ambiguous). The same function is applied to the ideal cycle,
    so the *difference* user-ideal is what we use. Cycle must start mid-inhale."""
    # both ends sit at mid-inhale, so a straight line between them is residual drift
    cyc = cyc - np.linspace(cyc[0], cyc[-1], len(cyc))
    lo, hi = np.percentile(cyc, [5, 95])
    if hi - lo < 1e-9:
        return None
    c = (cyc - lo) / (hi - lo)
    up = np.where(c >= hi_t)[0]
    if len(up) == 0:
        return None
    i_hs = up[0]                                    # first arrival at the top
    dn = np.where(c[i_hs:] <= lo_t)[0]
    if len(dn) == 0:
        return None
    i_ls = i_hs + dn[0]                             # first arrival at the bottom
    i_he = i_hs + np.where(c[i_hs:i_ls] >= hi_t)[0][-1] + 1   # last sample at top (ripple-proof)
    i_le = np.where(c <= lo_t)[0][-1] + 1           # last sample at bottom
    return {"inhale": (i_hs + len(c) - i_le) / fs / 0.7,   # 0.15->0.85 is ~70% of a ramp
            "hold_in": (i_he - i_hs) / fs,
            "exhale": (i_ls - i_he) / fs / 0.7,
            "hold_out": (i_le - i_ls) / fs}


PATTERNS = load_patterns()

REQUIRED_PATTERNS = [
    "bhastrika", "kapalabhati", "nadi_shodhana", "anuloma_viloma",
    "bhramari", "ujjayi", "sama_vritti", "sheetali", "sheetkari", "surya_bhedana"
]

def verify_patterns(patterns: dict | None = None) -> None:
    """Startup verification: fails loudly if any problems or missing patterns."""
    pats = patterns if patterns is not None else PATTERNS
    if LOAD_PROBLEMS:
        msg = "Pattern loading failed with problems:\n" + "\n".join(f"  - {p}" for p in LOAD_PROBLEMS)
        raise RuntimeError(msg)
    missing = [k for k in REQUIRED_PATTERNS if k not in pats]
    if missing:
        raise RuntimeError(f"Missing required pranayama pattern(s): {missing}. All 10 website patterns must be present.")

verify_patterns(PATTERNS)


def analyse_cycle(idx, start_s, cyc, ideal, bounds, p, fs, radius_frac) -> CycleResult:
    L = len(ideal)
    # time-normalise the user cycle to the ideal length -> DTW judges SHAPE,
    # tempo is scored separately (otherwise warping would hide it)
    u = np.interp(np.linspace(0, len(cyc) - 1, L), np.arange(len(cyc)), cyc)
    u, ref = znorm(u), znorm(ideal)
    radius = max(2, int(round(radius_frac * L)))
    d, pu, pi, _ = dtw_banded(u, ref, radius)

    tempo = len(cyc) / L
    ideal_ph = {"inhale": p.inhale, "hold_in": p.hold_in, "exhale": p.exhale, "hold_out": p.hold_out}
    m_user, m_ideal = _phase_measures(cyc, fs), _phase_measures(ideal, fs)
    if m_user and m_ideal:   # ideal duration + (how much longer/shorter the user's shape is)
        user_ph = {k: max(0.0, ideal_ph[k] + m_user[k] - m_ideal[k]) for k in ideal_ph}
    else:
        user_ph = dict(ideal_ph)

    shape_score = 100 * np.exp(-d / 0.35)
    tempo_score = 100 * np.exp(-abs(np.log(tempo)) / 0.20)
    return CycleResult(idx, start_s, len(cyc) / fs, d, shape_score, tempo, tempo_score,
                       0.75 * shape_score + 0.25 * tempo_score,
                       {"ideal": ideal_ph, "user": user_ph}, (pu, pi))


# ----------------------------------------------------------------------------
# 6. Whole-session analysis
# ----------------------------------------------------------------------------
def analyse_session(x: np.ndarray, p: Pattern, fs: float,
                    cycle_radius_frac: float = 0.15, session_window_s: float = 5.0):
    ideal_cycle, bounds = aligned_cycle(p, fs)
    spans = segment_cycles(x, fs, min_cycle_s=0.5 * p.cycle_s)
    cycles = [analyse_cycle(k, a / fs, x[a:b], ideal_cycle, bounds, p, fs, cycle_radius_frac)
              for k, (a, b) in enumerate(spans)]

    tiled = znorm(tiled_template(p, fs, len(x)))
    radius = int(session_window_s * fs)
    d, pu, pi, D = dtw_banded(x, tiled, radius)

    scores = np.array([c.score for c in cycles]) if cycles else np.array([0.0])
    tempos = np.array([c.tempo_ratio for c in cycles]) if cycles else np.array([1.0])
    summary = {
        "n_cycles_found": len(cycles),
        "n_cycles_expected": len(x) / fs / p.cycle_s,
        "mean_cycle_score": float(scores.mean()),
        "consistency_std": float(scores.std()),
        "median_tempo_ratio": float(np.median(tempos)),
        "session_dtw_dist": float(d),
        "session_score": float(100 * np.exp(-d / 0.8)),
    }
    return {"cycles": cycles, "tiled": tiled, "session_path": (pu, pi),
            "session_window_s": session_window_s, "summary": summary}


# ----------------------------------------------------------------------------
# 7. Synthetic user (so you can run & see without any hardware / team input)
# ----------------------------------------------------------------------------
def simulate_user(p: Pattern, fs=10.0, duration_s=300.0, seed=7, tempo_drift=0.06,
                  sloppiness=0.12, noise=0.08, lapse_cycle=4,
                  resync=None, lag_s=0.0, guide_cycle=None):
    rng = np.random.default_rng(seed)
    pieces, total, k = [], 0.0, 0
    while total < duration_s + p.cycle_s:
        drift = 1 + tempo_drift * total / duration_s            # slowly slowing down
        f = lambda: max(0.3, drift * (1 + rng.normal(0, sloppiness)))
        q = Pattern("u", p.inhale * f(), p.hold_in * f() * (0.4 if k == lapse_cycle else 1),
                    p.exhale * f(), p.hold_out * f())
        if resync is not None:   # a guided user re-joins the guide's clock every cycle
            gc = guide_cycle or p.cycle_s              # stay locked to the guide's clock: the next
            end = (k + 1) * gc + rng.normal(0, resync * gc)   # cycle ends near a multiple of the guide
            target = max(0.5 * gc, end - total)                # cycle, so timing errors do not pile up
            q = Pattern("u", *(v * target / q.cycle_s for v in (q.inhale, q.hold_in, q.exhale, q.hold_out)))
        amp, off = rng.uniform(0.75, 1.2), rng.normal(0, 0.05)
        c = one_cycle(q, fs) * amp + off
        pieces.append(c)
        total += len(c) / fs
        k += 1
    x = np.concatenate(pieces)
    if lag_s:                                   # human reaction delay
        x = np.concatenate([np.full(int(lag_s * fs), x[0]), x])
    x = x[: int(duration_s * fs)]
    t = np.arange(len(x)) / fs
    x = x + 0.3 * np.sin(2 * np.pi * 0.008 * t) + rng.normal(0, noise, len(x))   # drift + noise
    return t, x


# ----------------------------------------------------------------------------
# 8. Visualisation
# ----------------------------------------------------------------------------
def save_figure(fig, path: str, **kw) -> str:
    """Save a figure; if Windows refuses (file open in another program, antivirus, OneDrive,
    locked folder ...) try a time-stamped name, then the temp folder. Returns where it went."""
    path = os.path.abspath(path)
    stem, ext = os.path.splitext(path)
    tries = [path, f"{stem}_{time.strftime('%H%M%S')}{ext}",
             os.path.join(tempfile.gettempdir(), os.path.basename(stem) + ext)]
    last = None
    for i, p_ in enumerate(tries):
        try:
            os.makedirs(os.path.dirname(p_), exist_ok=True)
            fig.savefig(p_, **kw)
            if i:
                print(f"  note: could not write {path}\n        ({last})\n        saved here instead: {p_}")
            return p_
        except OSError as e:
            last = e
    raise OSError(f"could not save the picture anywhere. Last error: {last}")


def plot_dashboard(x, p, fs, res, save=None, show=True):
    import matplotlib
    if not show:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    t = np.arange(len(x)) / fs
    cyc, S = res["cycles"], res["summary"]
    fig = plt.figure(figsize=(15, 11), constrained_layout=True)
    gs = fig.add_gridspec(3, 2)

    ax = fig.add_subplot(gs[0, :])
    ax.plot(t, res["tiled"], color="#1f77b4", lw=1.6, label="Ideal (tiled)")
    ax.plot(t, x, color="#d62728", lw=1.0, alpha=.85, label="User (processed)")
    ax.set(title=f"{p.name} | session score {S['session_score']:.0f}/100 | "
                 f"{S['n_cycles_found']} cycles found (≈{S['n_cycles_expected']:.1f} expected)",
           xlabel="time (s)", ylabel="z-score")
    ax.legend(loc="upper right")

    ax = fig.add_subplot(gs[1, 0])
    pu, pi = res["session_path"]
    ax.plot(t[pu], t[pi] - t[pu], color="k", lw=1)
    w = res["session_window_s"]
    ax.axhline(w, ls="--", c="gray"); ax.axhline(-w, ls="--", c="gray")
    ax.set(title=f"Warping path (ideal time − user time), band ±{w:.0f}s",
           xlabel="user time (s)", ylabel="offset (s)")

    ax = fig.add_subplot(gs[1, 1])
    if cyc:
        sc = [c.score for c in cyc]
        colors = ["#2ca02c" if s >= 75 else "#ff7f0e" if s >= 50 else "#d62728" for s in sc]
        ax.bar([c.index + 1 for c in cyc], sc, color=colors)
        ax.axhline(75, ls="--", c="gray")
    ax.set(title="Score per breath cycle", xlabel="cycle #", ylabel="score", ylim=(0, 100))

    # worst cycle: alignment lines
    ax = fig.add_subplot(gs[2, 0])
    if cyc:
        worst = min(cyc, key=lambda c: c.score)
        ideal, _ = aligned_cycle(p, fs)
        ref = znorm(ideal)
        a = int(round(worst.start_s * fs)); b = a + int(round(worst.duration_s * fs))
        u = znorm(np.interp(np.linspace(0, b - a - 1, len(ideal)), np.arange(b - a), x[a:b]))
        off = 3.0
        ax.plot(ref + off, c="#1f77b4", label="ideal"); ax.plot(u, c="#d62728", label="user")
        pu_, pi_ = worst.path
        for k in range(0, len(pu_), max(1, len(pu_) // 40)):
            ax.plot([pu_[k], pi_[k]], [u[pu_[k]], ref[pi_[k]] + off], c="gray", lw=.4)
        ax.set(title=f"DTW alignment of worst cycle (#{worst.index + 1}, score {worst.score:.0f})",
               xlabel="sample (cycle normalised)"); ax.legend()

    ax = fig.add_subplot(gs[2, 1]); ax.axis("off")
    lines = [f"Mean cycle score : {S['mean_cycle_score']:.1f}",
             f"Consistency (std): {S['consistency_std']:.1f}",
             f"Median tempo     : {S['median_tempo_ratio']:.2f}x ideal pace",
             "", "Average phase timing (s)   ideal -> you"]
    if cyc:
        for ph in ("inhale", "hold_in", "exhale", "hold_out"):
            iu = cyc[0].phases["ideal"][ph]
            uu = np.median([c.phases["user"][ph] for c in cyc])
            lines.append(f"  {ph:9s} {iu:5.1f} -> {uu:5.1f}")
    ax.text(0, 1, "\n".join(lines), va="top", family="monospace", fontsize=11)

    if save:
        print("saved", save_figure(fig, save, dpi=130))
    if show:
        plt.show()


def feedback(res, p: Pattern) -> list[str]:
    """Human-readable tips you can show on the website."""
    out, cyc = [], res["cycles"]
    if not cyc:
        return ["Could not detect breath cycles - check signal quality."]
    ratio = res["summary"]["median_tempo_ratio"]
    if ratio > 1.15: out.append(f"You are breathing ~{(ratio - 1) * 100:.0f}% slower than the guide.")
    if ratio < 0.87: out.append(f"You are breathing ~{(1 - ratio) * 100:.0f}% faster than the guide.")
    for ph in ("hold_in", "hold_out", "exhale", "inhale"):
        ideal = getattr(p, ph)
        if ideal <= 0.5:
            continue
        med = np.median([c.phases["user"][ph] for c in cyc])
        if med < 0.75 * ideal:
            out.append(f"Your {ph.replace('_', ' ')} is short ({med:.1f}s vs {ideal:.1f}s).")
        elif med > 1.25 * ideal:
            out.append(f"Your {ph.replace('_', ' ')} is long ({med:.1f}s vs {ideal:.1f}s).")
    short = [c.index + 1 for c in cyc if any(
        getattr(p, ph) >= 1 and c.phases["user"][ph] < 0.6 * getattr(p, ph)
        for ph in ("hold_in", "hold_out"))]
    if short:
        out.append(f"Breath holds were cut short in cycle(s): {short[:8]}")
    bad = [c.index + 1 for c in cyc if c.score < 50]
    if bad:
        out.append(f"Weak cycles overall: {bad[:8]}")
    return out or ["Great session - very close to the ideal pattern!"]


# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pattern", default="sama_vritti", choices=PATTERNS)
    ap.add_argument("--csv", help="CSV: time_seconds,value")
    ap.add_argument("--fs", type=float, default=10.0)
    ap.add_argument("--duration", type=float, default=300.0)
    ap.add_argument("--window", type=float, default=5.0, help="session band half-width (s)")
    ap.add_argument("--save"); ap.add_argument("--no-show", action="store_true")
    a = ap.parse_args()

    p = PATTERNS[a.pattern]
    if a.csv:
        d = np.loadtxt(a.csv, delimiter=",", skiprows=0 if _is_num(a.csv) else 1)
        t, raw = d[:, 0], d[:, 1]
    else:
        t, raw = simulate_user(p, a.fs, a.duration)

    x = preprocess(t, raw, a.fs)
    res = analyse_session(x, p, a.fs, session_window_s=a.window)

    print(f"\nPattern: {p.name}  (ideal cycle {p.cycle_s:.0f}s)")
    for k, v in res["summary"].items():
        print(f"  {k:22s} {v:.2f}" if isinstance(v, float) else f"  {k:22s} {v}")
    print("\nFeedback:")
    for line in feedback(res, p):
        print("  -", line)
    plot_dashboard(x, p, a.fs, res, save=a.save, show=not a.no_show)


def _is_num(path):
    with open(path) as f:
        try:
            float(f.readline().split(",")[0]); return True
        except ValueError:
            return False


if __name__ == "__main__":
    main()
