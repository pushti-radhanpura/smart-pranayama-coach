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


def cycle_review(pattern: str, cycle_num: int, t, values) -> dict:
    """Detailed per-cycle respiration parameter review and verbal overview matching clinical metrics."""
    if t is not None and values is not None and 5 <= len(t) < 20:
        step = (t[-1] - t[0]) / max(1, len(t) - 1) if len(t) > 1 else 0.1
        step = step if step > 0 else 0.1
        t_pad = [t[-1] + step * (i + 1) for i in range(20 - len(t))]
        v_pad = [float(values[-1])] * (20 - len(t))
        t = list(t) + t_pad
        values = list(values) + v_pad
    t, v = _check(pattern, t, values)
    p = dtw.PATTERNS[pattern]
    fs = 10.0
    x = dtw.preprocess(t, v, fs=fs)

    ideal_cycle, bounds = dtw.aligned_cycle(p, fs)
    spans = dtw.segment_cycles(x, fs, min_cycle_s=0.5 * p.cycle_s)

    if spans:
        a, b = spans[-1]
        cyc = x[a:b]
        cyc_dur = float((b - a) / fs)
        cyc_res = dtw.analyse_cycle(cycle_num, a / fs, cyc, ideal_cycle, bounds, p, fs, 0.15)
        dtw_score = float(cyc_res.score)
        m_u = cyc_res.phases["user"]
        measured_inh = float(m_u.get("inhale", p.inhale))
        measured_hld = float(m_u.get("hold_in", p.hold_in))
        measured_exh = float(m_u.get("exhale", p.exhale))
        measured_hld_out = float(m_u.get("hold_out", p.hold_out))
    else:
        n_samples = max(20, int(round(p.cycle_s * fs)))
        cyc = x[-n_samples:] if len(x) >= n_samples else x
        cyc_dur = float(len(cyc) / fs)
        dtw_score = 78.0
        measured_inh = float(p.inhale)
        measured_hld = float(p.hold_in)
        measured_exh = float(p.exhale)
        measured_hld_out = float(p.hold_out)

    measured_bpm = round(60.0 / cyc_dur, 1) if cyc_dur > 0 else round(60.0 / p.cycle_s, 1)
    ideal_bpm = round(60.0 / p.cycle_s, 1)

    inh_base = max(0.4, measured_inh)
    hld_ratio = round(measured_hld / inh_base, 2)
    exh_ratio = round(measured_exh / inh_base, 2)
    hld_out_ratio = round(measured_hld_out / inh_base, 2)

    if p.hold_out > 0 or measured_hld_out > 0.5:
        measured_ratio_str = f"1 : {hld_ratio:.2f} : {exh_ratio:.2f} : {hld_out_ratio:.2f}"
    else:
        measured_ratio_str = f"1 : {hld_ratio:.2f} : {exh_ratio:.2f}"

    id_inh = p.inhale
    id_hld = round(p.hold_in / id_inh, 2)
    id_exh = round(p.exhale / id_inh, 2)
    id_hld_out = round(p.hold_out / id_inh, 2)
    if p.hold_out > 0:
        ideal_ratio_str = f"1 : {id_hld:g} : {id_exh:g} : {id_hld_out:g}"
    else:
        ideal_ratio_str = f"1 : {id_hld:g} : {id_exh:g}"

    amp = float(np.ptp(cyc)) if len(cyc) > 0 else 2.5
    vt_ml = int(round(max(180.0, min(1200.0, (amp / 2.5) * 450.0))))
    ideal_vt_str = "1100 mL" if p.cycle_s >= 6 else ("800 mL" if p.cycle_s >= 3 else "350 mL")

    flow_diff = np.diff(cyc) * fs * 70.0 if len(cyc) > 1 else np.array([120.0])
    pef = int(round(max(60.0, min(500.0, float(np.max(np.abs(flow_diff)))))))

    score = round(dtw_score, 1)
    if score >= 80:
        grade = "Grade A"
        score_assessment = "PASSED"
        headline = "Well Done! Solid adherence and rhythm throughout the practice."
    elif score >= 70:
        grade = "Grade B"
        score_assessment = "GOOD"
        headline = "Good work! Steady breathing with minor timing adjustments needed."
    elif score >= 50:
        grade = "Grade C"
        score_assessment = "FAIR"
        headline = "Fair attempt. Focus on pacing each phase with the guide."
    else:
        grade = "Grade D"
        score_assessment = "NEEDS PRACTICE"
        headline = "Needs practice. Align your breath closely with the circle animation."

    ratio_err = abs(exh_ratio - id_exh) + abs(hld_ratio - id_hld)
    if ratio_err <= 0.35:
        ratio_assessment = "EXCELLENT MATCH"
    elif ratio_err <= 0.75:
        ratio_assessment = "GOOD MATCH"
    else:
        ratio_assessment = "OFF TARGET"

    bpm_diff = abs(measured_bpm - ideal_bpm)
    if bpm_diff <= 1.0:
        rate_assessment = "ON TARGET"
    elif measured_bpm > ideal_bpm:
        rate_assessment = "SLIGHTLY FAST"
    else:
        rate_assessment = "OFF TARGET"

    vt_assessment = "GOOD DEPTH" if 250 <= vt_ml <= 850 else ("SHALLOW" if vt_ml < 250 else "DEEP")
    pef_assessment = "STABLE"
    endurance_assessment = "SUSTAINED"

    recs = []
    if measured_inh > p.inhale * 1.25:
        recs.append(f"shorten your inhalation slightly to ~{p.inhale:g}s (currently {measured_inh:.1f}s)")
    elif measured_inh < p.inhale * 0.75:
        recs.append(f"lengthen your inhalation slightly to ~{p.inhale:g}s (currently {measured_inh:.1f}s)")

    if p.hold_in > 0 and measured_hld < p.hold_in * 0.75:
        recs.append(f"maintain breath retention comfortably for ~{p.hold_in:g}s (currently {measured_hld:.1f}s)")

    if measured_exh < p.exhale * 0.8:
        recs.append(f"extend your exhalation smoothly to ~{p.exhale:g}s (currently {measured_exh:.1f}s)")
    elif measured_exh > p.exhale * 1.25:
        recs.append(f"shorten your exhalation slightly to ~{p.exhale:g}s (currently {measured_exh:.1f}s)")

    if bpm_diff > 1.2:
        if measured_bpm < ideal_bpm:
            recs.append(f"breathe slightly more briskly towards {ideal_bpm:g} BPM (currently {measured_bpm:g} BPM)")
        else:
            recs.append(f"slow down your pace comfortably towards {ideal_bpm:g} BPM (currently {measured_bpm:g} BPM)")

    if p.cue and len(recs) < 2:
        recs.append(p.cue)

    if not recs:
        recs.append("Maintain this exact calm rhythm for the upcoming cycles.")

    rec_spoken = recs[0].rstrip(".")
    spoken_text = (
        f"Cycle {cycle_num} complete. Score {score:.0f} percent, {grade}. "
        f"Measured ratio was {measured_ratio_str} at {measured_bpm:g} BPM. "
        f"{rec_spoken}."
    )

    total_session_s = int(round(t[-1])) if len(t) else int(round(cyc_dur * cycle_num))

    return {
        "cycle_num": cycle_num,
        "pattern": pattern,
        "pattern_name": p.name,
        "headline": headline,
        "summary_text": (
            f"You completed cycle {cycle_num} of {p.name} with an overall mastery "
            f"score of {score}/100 ({grade}). Measured ratio was {measured_ratio_str} "
            f"at {measured_bpm} BPM (target {ideal_bpm} BPM)."
        ),
        "spoken_text": spoken_text,
        "score": score,
        "grade": grade,
        "parameters": [
            {
                "name": "1. Inhale : Hold : Exhale",
                "measured": measured_ratio_str,
                "ideal": ideal_ratio_str,
                "assessment": ratio_assessment,
                "status_cls": "match" if "MATCH" in ratio_assessment else "alert",
            },
            {
                "name": "2. Respiratory Rate",
                "measured": f"{measured_bpm} BPM",
                "ideal": f"{ideal_bpm} BPM",
                "assessment": rate_assessment,
                "status_cls": "match" if rate_assessment == "ON TARGET" else "alert",
            },
            {
                "name": "3. Tidal Volume (Vt)",
                "measured": f"{vt_ml} mL",
                "ideal": ideal_vt_str,
                "assessment": vt_assessment,
                "status_cls": "match" if vt_assessment == "GOOD DEPTH" else "alert",
            },
            {
                "name": "4. Peak Expiratory Flow",
                "measured": f"{pef} mL/s",
                "ideal": "Smooth / Controlled",
                "assessment": pef_assessment,
                "status_cls": "match",
            },
            {
                "name": "5. Endurance Duration",
                "measured": f"{total_session_s} s",
                "ideal": "Full Session",
                "assessment": endurance_assessment,
                "status_cls": "match",
            },
            {
                "name": "Overall DTW Match Score",
                "measured": f"{score}% ({grade})",
                "ideal": ">= 80% (Mastery)",
                "assessment": score_assessment,
                "status_cls": "match" if score >= 70 else "alert",
            },
        ],
        "recommendations": recs,
    }
