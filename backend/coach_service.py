"""
coach_service.py - the web-framework-independent core of the pranayama coach backend.
Handles sessions, pattern lookups, live evaluations, final reviews, and DTW session comparisons.
"""
from __future__ import annotations

import threading
import numpy as np

import pranayama_dtw as dtw
from pranayama_coach import LiveCoach

MAX_SESSIONS = 500
_sessions: dict[str, tuple[str, LiveCoach]] = {}
_lock = threading.Lock()


class ServiceError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def list_patterns() -> dict:
    return {
        k: {
            "name": p.name,
            "cycle_s": round(p.cycle_s, 2),
            "inhale": round(p.inhale, 2),
            "hold_in": round(p.hold_in, 2),
            "exhale": round(p.exhale, 2),
            "hold_out": round(p.hold_out, 2),
            "eyes_closed": p.eyes_closed,
            "cue": p.cue,
        }
        for k, p in dtw.PATTERNS.items()
    }


def _check(pattern: str, t, values):
    if pattern not in dtw.PATTERNS:
        raise ServiceError(404, f"Unknown pattern '{pattern}'. Available: {', '.join(dtw.PATTERNS)}")
    try:
        t, v = np.asarray(t, float), np.asarray(values, float)
    except (TypeError, ValueError):
        raise ServiceError(400, "t and values must be lists of numbers")
    if t.ndim != 1 or t.shape != v.shape:
        raise ServiceError(400, "t and values must be lists of the same length")
    if len(t) < 20:
        raise ServiceError(400, "need at least 20 samples")
    if len(t) > 100_000:
        raise ServiceError(413, "too many samples")
    if not (np.all(np.isfinite(t)) and np.all(np.isfinite(v))):
        raise ServiceError(400, "t and values must not contain NaN or infinity")
    if np.any(np.diff(t) <= 0):
        raise ServiceError(400, "t must keep increasing (seconds since the guide started)")
    return t, v


def evaluate_chunk(session_id: str, pattern: str, t, values, guided: bool = True) -> dict:
    t, v = _check(pattern, t, values)
    with _lock:
        held = _sessions.get(session_id)
        if held is None or held[0] != pattern:
            if len(_sessions) >= MAX_SESSIONS:
                _sessions.pop(next(iter(_sessions)))          # drop the oldest
            held = (pattern, LiveCoach(dtw.PATTERNS[pattern], guided=guided))
            _sessions[session_id] = held
    verdict = held[1].evaluate(t, v, now=float(t[-1]))
    return {
        "status": verdict.status,
        "message": verdict.message,
        "speak": verdict.speak,
        "stop_session": verdict.stop_session,
        "score": round(float(verdict.score), 1),
    }


def finish_session(session_id: str, pattern: str, t, values) -> dict:
    t, v = _check(pattern, t, values)
    with _lock:
        held = _sessions.pop(session_id, None)
    coach = held[1] if held and held[0] == pattern else LiveCoach(dtw.PATTERNS[pattern])
    return {"review": coach.final_review(t, v)}


def compare_session(pattern: str, t, values) -> dict:
    """Run Sakoe-Chiba DTW session comparison on the user waveform against the ideal template."""
    t, v = _check(pattern, t, values)
    p = dtw.PATTERNS[pattern]
    fs = 10.0
    x = dtw.preprocess(t, v, fs=fs)
    res = dtw.analyse_session(x, p, fs=fs)

    summary = res["summary"]
    session_dtw_dist = float(summary["session_dtw_dist"])
    session_score = float(summary["session_score"])
    cycle_scores = [float(c.score) for c in res["cycles"]]

    # Downsample DTW path (pu, pi) to max 300 points
    pu, pi = res["session_path"]
    n_path = len(pu)
    if n_path > 300:
        idx = np.round(np.linspace(0, n_path - 1, 300)).astype(int)
        pu_down = [int(val) for val in pu[idx]]
        pi_down = [int(val) for val in pi[idx]]
    else:
        pu_down = [int(val) for val in pu]
        pi_down = [int(val) for val in pi]

    # Downsample ideal tiled curve and user preprocessed curve to max ~600 points
    n_pts = len(x)
    if n_pts > 600:
        idx_pts = np.round(np.linspace(0, n_pts - 1, 600)).astype(int)
        user_down = [float(round(val, 4)) for val in x[idx_pts]]
        ideal_down = [float(round(val, 4)) for val in res["tiled"][idx_pts]]
        t_down = [float(round(val, 2)) for val in (idx_pts / fs)]
    else:
        user_down = [float(round(val, 4)) for val in x]
        ideal_down = [float(round(val, 4)) for val in res["tiled"]]
        t_down = [float(round(val, 2)) for val in (np.arange(n_pts) / fs)]

    tips = dtw.feedback(res, p)

    return {
        "pattern": pattern,
        "pattern_name": p.name,
        "session_dtw_dist": round(session_dtw_dist, 4),
        "session_score": round(session_score, 1),
        "cycle_scores": [round(s, 1) for s in cycle_scores],
        "dtw_path": {"user_idx": pu_down, "ideal_idx": pi_down},
        "time": t_down,
        "user_preprocessed": user_down,
        "ideal_tiled": ideal_down,
        "summary": {
            "n_cycles_found": int(summary["n_cycles_found"]),
            "n_cycles_expected": round(float(summary["n_cycles_expected"]), 1),
            "mean_cycle_score": round(float(summary["mean_cycle_score"]), 1),
            "consistency_std": round(float(summary["consistency_std"]), 1),
            "median_tempo_ratio": round(float(summary["median_tempo_ratio"]), 2),
            "session_dtw_dist": round(float(summary["session_dtw_dist"]), 4),
            "session_score": round(float(summary["session_score"]), 1),
        },
        "feedback": tips,
    }
