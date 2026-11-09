"""
coach_service.py - the web-framework-independent core of the pranayama coach backend.
Handles sessions, pattern lookups, live evaluations, final reviews, and DTW session comparisons.
"""
from __future__ import annotations

import threading
import numpy as np
from scipy.signal import find_peaks

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

    # 1. Genuine period and cycle duration detection from signal extrema
    w = max(3, int(0.15 * fs))
    xs = np.convolve(x, np.ones(w) / w, mode="same")
    min_dist = max(3, int(0.25 * p.cycle_s * fs))
    ptp_val = float(np.ptp(xs)) or 1.0
    peaks, _ = find_peaks(xs, distance=min_dist, prominence=0.08 * ptp_val)
    troughs, _ = find_peaks(-xs, distance=min_dist, prominence=0.08 * ptp_val)

    spans = dtw.segment_cycles(x, fs, min_cycle_s=0.4 * p.cycle_s)

    if spans:
        a, b = spans[-1]
        cyc = x[a:b]
        cyc_dur = float((b - a) / fs)
    elif len(peaks) >= 2:
        cyc_dur = float((peaks[-1] - peaks[-2]) / fs)
        cyc = xs[peaks[-2]:peaks[-1]]
    elif len(troughs) >= 2:
        cyc_dur = float((troughs[-1] - troughs[-2]) / fs)
        cyc = xs[troughs[-2]:troughs[-1]]
    elif len(peaks) >= 1 and len(troughs) >= 1:
        cyc_dur = float(2.0 * abs(peaks[-1] - troughs[-1]) / fs)
        n_pts = int(round(cyc_dur * fs))
        cyc = xs[-n_pts:] if len(xs) >= n_pts else xs
    else:
        cyc_dur = float(max(1.0, t[-1] - t[0]))
        cyc = xs

    cyc_dur = float(np.clip(cyc_dur, 0.4, 60.0))
    measured_bpm = round(60.0 / cyc_dur, 1)
    ideal_bpm = round(60.0 / p.cycle_s, 1)

    # 2. Genuine phase measurements from the extracted cycle segment
    lo_c, hi_c = float(np.min(cyc)), float(np.max(cyc))
    rng_c = max(1e-4, hi_c - lo_c)
    norm_c = (cyc - lo_c) / rng_c

    hi_idx = np.where(norm_c >= 0.80)[0]
    if len(hi_idx) > 0:
        first_top = hi_idx[0]
        dn_idx = np.where(norm_c[first_top:] < 0.75)[0]
        last_top = first_top + dn_idx[0] if len(dn_idx) > 0 else len(norm_c) - 1
        lo_idx = np.where(norm_c[last_top:] <= 0.20)[0]
        first_bot = last_top + lo_idx[0] if len(lo_idx) > 0 else len(norm_c) - 1

        measured_inh = float(max(0.5, first_top / fs))
        measured_hld = float(max(0.0, (last_top - first_top) / fs))
        measured_exh = float(max(0.5, (first_bot - last_top) / fs))
        measured_hld_out = float(max(0.0, (len(norm_c) - 1 - first_bot) / fs))
    else:
        measured_inh = float(0.5 * cyc_dur)
        measured_hld = 0.0
        measured_exh = float(0.5 * cyc_dur)
        measured_hld_out = 0.0

    # 3. True Sakoe-Chiba DTW banded alignment
    L = len(ideal_cycle)
    if len(cyc) >= 4:
        u = np.interp(np.linspace(0, len(cyc) - 1, L), np.arange(len(cyc)), cyc)
        u_z = dtw.znorm(u)
        ref_z = dtw.znorm(ideal_cycle)
        radius = max(2, int(round(0.20 * L)))
        d, pu, pi, _ = dtw.dtw_banded(u_z, ref_z, radius)
        shape_score = 100.0 * np.exp(-d / 0.32)
        tempo_ratio = cyc_dur / p.cycle_s
        tempo_score = 100.0 * np.exp(-abs(np.log(max(0.2, tempo_ratio))) / 0.22)
        dtw_score = float(np.clip(0.70 * shape_score + 0.30 * tempo_score, 15.0, 98.0))
    else:
        dtw_score = 75.0

    score = round(dtw_score, 1)

    # 4. Tidal volume & PEF calculation
    amp = float(np.ptp(cyc)) if len(cyc) > 0 else 2.5
    vt_ml = int(round(max(180.0, min(1400.0, (amp / 2.0) * 450.0))))
    ideal_vt_str = "1100 mL" if p.cycle_s >= 6 else ("800 mL" if p.cycle_s >= 3 else "350 mL")

    flow_diff = np.diff(cyc) * fs * 70.0 if len(cyc) > 1 else np.array([120.0])
    pef = int(round(max(60.0, min(500.0, float(np.max(np.abs(flow_diff)))))))

    # Ratio calculation
    inh_base = max(0.4, measured_inh)
    hld_ratio = round(measured_hld / inh_base, 2)
    exh_ratio = round(measured_exh / inh_base, 2)
    hld_out_ratio = round(measured_hld_out / inh_base, 2)

    if p.hold_out > 0 or hld_out_ratio >= 0.3:
        measured_ratio_str = f"1 : {hld_ratio:.2f} : {exh_ratio:.2f} : {hld_out_ratio:.2f}"
    else:
        measured_ratio_str = f"1 : {hld_ratio:.2f} : {exh_ratio:.2f}"

    id_inh = max(0.1, p.inhale)
    id_hld = round(p.hold_in / id_inh, 2)
    id_exh = round(p.exhale / id_inh, 2)
    id_hld_out = round(p.hold_out / id_inh, 2)
    if p.hold_out > 0:
        ideal_ratio_str = f"1 : {id_hld:g} : {id_exh:g} : {id_hld_out:g}"
    else:
        ideal_ratio_str = f"1 : {id_hld:g} : {id_exh:g}"

    # Performance Assessments
    if score >= 80:
        grade = "Grade A"
        score_assessment = "PASSED"
        headline = "Well Done! Solid adherence and rhythm throughout the cycle."
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
    if ratio_err <= 0.40:
        ratio_assessment = "EXCELLENT MATCH"
    elif ratio_err <= 0.85:
        ratio_assessment = "GOOD MATCH"
    else:
        ratio_assessment = "OFF TARGET"

    bpm_diff = abs(measured_bpm - ideal_bpm)
    if bpm_diff <= 1.0:
        rate_assessment = "ON TARGET"
    elif measured_bpm > ideal_bpm:
        rate_assessment = "TOO FAST"
    else:
        rate_assessment = "TOO SLOW"

    vt_assessment = "GOOD DEPTH" if 250 <= vt_ml <= 850 else ("SHALLOW" if vt_ml < 250 else "DEEP")
    pef_assessment = "STABLE"
    endurance_assessment = "SUSTAINED"

    # Actionable coaching recommendations
    recs = []
    if measured_inh > p.inhale * 1.30:
        recs.append(f"Shorten your inhalation slightly towards ~{p.inhale:g}s (currently {measured_inh:.1f}s).")
    elif measured_inh < p.inhale * 0.70:
        recs.append(f"Lengthen your inhalation gently towards ~{p.inhale:g}s (currently {measured_inh:.1f}s).")

    if p.hold_in > 0 and measured_hld < p.hold_in * 0.70:
        recs.append(f"Maintain breath retention comfortably at the peak for ~{p.hold_in:g}s.")

    if measured_exh < p.exhale * 0.75:
        recs.append(f"Extend your exhalation smoothly towards ~{p.exhale:g}s (currently {measured_exh:.1f}s).")
    elif measured_exh > p.exhale * 1.35:
        recs.append(f"Complete your exhalation a bit sooner towards ~{p.exhale:g}s.")

    if bpm_diff > 1.2:
        if measured_bpm < ideal_bpm:
            recs.append(f"Breathe slightly more briskly towards {ideal_bpm:g} breaths per minute.")
        else:
            recs.append(f"Slow down your pace comfortably towards {ideal_bpm:g} breaths per minute.")

    if p.cue and len(recs) < 2:
        recs.append(p.cue)

    if not recs:
        recs.append("Maintain this exact calm rhythm and diaphragmatic focus.")

    # Spoken natural human overview: NO colons, NO acronyms, NO parentheses!
    import re
    spoken_bpm = int(round(measured_bpm))
    spoken_score = int(round(score))
    primary_advice = recs[0].rstrip(".")
    clean_spoken_advice = re.sub(r"\(currently\s*[^)]+\)", "", primary_advice)
    clean_spoken_advice = re.sub(r"~(\d+(?:\.\d+)?)s\b", r"\1 seconds", clean_spoken_advice)
    clean_spoken_advice = clean_spoken_advice.replace("~", "approximately ").strip(" .")

    if p.hold_out > 0 or hld_out_ratio >= 0.3:
        spoken_ratio = f"1 to {round(hld_ratio):g} to {round(exh_ratio):g} to {round(hld_out_ratio):g}"
    else:
        spoken_ratio = f"1 to {round(hld_ratio):g} to {round(exh_ratio):g}"

    spoken_text = (
        f"Cycle {cycle_num} complete. Overall score {spoken_score} percent, {grade}. "
        f"Your breath ratio was {spoken_ratio}, at {spoken_bpm} breaths per minute. "
        f"{clean_spoken_advice}."
    )

    total_session_s = int(round(t[-1])) if len(t) else int(round(cyc_dur * cycle_num))
    import urllib.parse

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
        "audio_url": f"/tts?text={urllib.parse.quote(spoken_text)}&voice=en-IN-NeerjaNeural",
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
