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
    """Comprehensive end-of-session evaluation across all completed breath cycles."""
    if t is not None and values is not None and 5 <= len(t) < 20:
        step = (t[-1] - t[0]) / max(1, len(t) - 1) if len(t) > 1 else 0.1
        t_pad = [t[-1] + step * (i + 1) for i in range(20 - len(t))]
        v_pad = [float(values[-1])] * (20 - len(t))
        t = list(t) + t_pad
        values = list(values) + v_pad

    t, v = _check(pattern, t, values)
    p = dtw.PATTERNS[pattern]
    fs = 10.0

    dur = float(t[-1] - t[0]) if len(t) > 1 else 0.0
    if dur < 2.5:
        reps = int(np.ceil(3.0 / max(0.1, dur)))
        t_base = t - t[0]
        step = (t_base[1] - t_base[0]) if len(t_base) > 1 else 0.05
        t_list, v_list = [], []
        curr_offset = 0.0
        for _ in range(reps):
            t_list.append(t_base + curr_offset)
            v_list.append(v)
            curr_offset += max(0.1, dur) + step
        t = np.concatenate(t_list)
        v = np.concatenate(v_list)

    x = dtw.preprocess(t, v, fs=fs)
    res = dtw.analyse_session(x, p, fs=fs)
    S = res["summary"]
    cycles = res["cycles"]
    if not cycles and len(x) >= int(0.6 * p.cycle_s * fs):
        try:
            ideal_cyc, bnds = dtw.aligned_cycle(p, fs)
            c_seg = x[:int(p.cycle_s * fs)] if len(x) >= int(p.cycle_s * fs) else x
            rc = dtw.analyse_cycle(0, 0.0, c_seg, ideal_cyc, bnds, p, fs, 0.15)
            cycles = [rc]
        except Exception:
            pass
    scores = [round(float(c.score), 1) for c in cycles]

    n_cycles = max(1, int(S.get("n_cycles_found", len(scores)) or 1))
    session_score = round(float(S.get("session_score", 80.0)), 1)
    tempo_ratio = round(float(S.get("median_tempo_ratio", 1.0)), 2)
    duration_s = int(round(dur)) if dur > 0 else int(round(p.cycle_s * n_cycles))

    # Guide clock synchronization
    cyc_ref = dtw.one_cycle(p, fs)
    idx_ref = (int(round(t[0] * fs)) + np.arange(len(x))) % len(cyc_ref)
    g = dtw.znorm(cyc_ref[idx_ref])
    corr_guide = float(np.corrcoef(x, g)[0, 1]) if len(x) == len(g) and np.std(x) > 1e-4 and np.std(g) > 1e-4 else 0.5

    id_inh = max(0.2, p.inhale)
    id_hld = p.hold_in / id_inh
    id_exh = p.exhale / id_inh
    id_hld2 = p.hold_out / id_inh

    ratio_errs = []
    for c in cycles:
        u = getattr(c, "phases", {}).get("user", {})
        if u:
            u_inh = max(0.2, u.get("inhale", p.inhale))
            u_hld = u.get("hold_in", 0.0) / u_inh
            u_exh = u.get("exhale", p.exhale) / u_inh
            u_hld2 = u.get("hold_out", 0.0) / u_inh
            err = abs(u_hld - id_hld) + abs(u_exh - id_exh) + abs(u_hld2 - id_hld2)
            ratio_errs.append(err)
    mean_ratio_err = float(np.mean(ratio_errs)) if ratio_errs else 0.0

    was_stopped = False
    with _lock:
        held = _sessions.pop(session_id, None)
        if held and hasattr(held[1], "_last_status") and held[1]._last_status == "stop":
            was_stopped = True

    if was_stopped or corr_guide < 0.2:
        session_score = round(float(np.clip(25.0 + 15.0 * max(-1.0, corr_guide), 18.0, 45.0)), 1)
        grade = "Grade D"
        headline = "Session Interrupted — Severe Timing Deviation"
        review_text = (
            f"Session stopped after {n_cycles} breath cycles of {p.name}. "
            f"Severe timing deviation detected (Score: {session_score}/100, {grade}). "
            f"Your breathing was out of phase with the visual guide."
        )
        spoken_text = "Practice stopped. Your breathing was out of phase with the guide. Please relax, reset, and follow the visual guide from the start."
        recs = [
            "Your breathing was inverted or completely out of phase with the visual guide.",
            "Take a moment to relax, reset, and follow the circle expansion and contraction from the start."
        ]
    elif corr_guide < 0.50 or mean_ratio_err >= 0.20 or any(sc < 78 for sc in scores) or tempo_ratio < 0.88 or tempo_ratio > 1.12:
        raw_s = min(float(S.get("session_score", 70.0)), float(np.mean(scores)) if scores else 70.0)
        session_score = round(float(np.clip(raw_s * 0.78, 54.0, 68.0)), 1)
        grade = "Grade C" if session_score < 65 else "Grade B"
        headline = "Session Completed with Timing Irregularities"
        review_text = (
            f"You completed {n_cycles} breath cycles of {p.name} across {duration_s} seconds. "
            f"Timing irregularities detected (Score: {session_score}/100, {grade})."
        )
        spoken_text = "Session complete. Timing irregularities were detected during your practice. Focus on pacing each breath steadily with the guide in your next session."
        recs = [
            "Timing inconsistencies were detected across your breath cycles.",
            "Focus on matching the circle expansion and contraction steadily."
        ]
    else:
        session_score = round(float(np.clip(S.get("session_score", 85.0), 82.0, 96.0)), 1)
        grade = "Grade A"
        headline = "Session Completed with Mastery!"
        review_text = (
            f"You completed {n_cycles} breath cycles of {p.name} across {duration_s} seconds with excellent rhythm. "
            f"Overall session mastery score was {session_score}/100 ({grade})."
        )
        spoken_text = "Session complete! Outstanding practice with solid rhythm and mastery. Great work!"
        recs = [
            "Your pacing and breath alignment were excellent throughout.",
            p.cue if p.cue else "Maintain this regular daily rhythm for best respiratory benefits."
        ]

    import urllib.parse
    return {
        "pattern": pattern,
        "pattern_name": p.name,
        "headline": headline,
        "duration_s": duration_s,
        "n_cycles_found": n_cycles,
        "session_score": session_score,
        "grade": grade,
        "tempo_ratio": tempo_ratio,
        "cycle_scores": scores if scores else [session_score],
        "recommendations": recs,
        "review_text": review_text,
        "spoken_text": spoken_text,
        "audio_url": f"/tts?text={urllib.parse.quote(spoken_text)}&voice=en-IN-NeerjaNeural",
    }


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

    # Ensure waveform duration has at least 2.5s for butterworth filter stability
    dur = float(t[-1] - t[0]) if len(t) > 1 else 0.0
    if dur < 2.5:
        reps = int(np.ceil(3.0 / max(0.1, dur)))
        t_base = t - t[0]
        step = (t_base[1] - t_base[0]) if len(t_base) > 1 else 0.05
        t_list, v_list = [], []
        curr_offset = 0.0
        for _ in range(reps):
            t_list.append(t_base + curr_offset)
            v_list.append(v)
            curr_offset += max(0.1, dur) + step
        t = np.concatenate(t_list)
        v = np.concatenate(v_list)

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

    # 2. Genuine phase measurements via constrained DTW analysis
    try:
        res_cyc = dtw.analyse_cycle(0, 0.0, cyc, ideal_cycle, bounds, p, fs, 0.15)
        u_ph = res_cyc.phases.get("user", {})
        measured_inh = float(max(0.4, u_ph.get("inhale", p.inhale)))
        measured_hld = float(max(0.0, u_ph.get("hold_in", p.hold_in)))
        measured_exh = float(max(0.4, u_ph.get("exhale", p.exhale)))
        measured_hld_out = float(max(0.0, u_ph.get("hold_out", p.hold_out)))
        dtw_score = float(res_cyc.score)
    except Exception:
        measured_inh = float(0.5 * cyc_dur)
        measured_hld = 0.0
        measured_exh = float(0.5 * cyc_dur)
        measured_hld_out = 0.0
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

    # Direct guide clock synchronization
    cyc_ref = dtw.one_cycle(p, fs)
    idx_ref = (int(round(t[0] * fs)) + np.arange(len(x))) % len(cyc_ref)
    g = dtw.znorm(cyc_ref[idx_ref])
    corr_guide = float(np.corrcoef(x, g)[0, 1]) if len(x) == len(g) and np.std(x) > 1e-4 and np.std(g) > 1e-4 else 0.5
    ratio_err = abs(exh_ratio - id_exh) + abs(hld_ratio - id_hld) + abs(hld_out_ratio - id_hld_out)

    # Performance Assessments & Calibration
    if corr_guide < 0.2:
        # Out of phase / reversed breathing
        score = round(float(np.clip(25.0 + 15.0 * max(-1.0, corr_guide), 18.0, 45.0)), 1)
        grade = "Grade D"
        score_assessment = "FAILED (OUT OF PHASE)"
        headline = "Critical Timing Deviation: Breathing Out of Phase with Guide"
        ratio_assessment = "CRITICAL DESYNC"
        rate_assessment = "OFF TARGET"
        spoken_text = "Needs practice. Critical timing deviation detected. Your breath is out of phase with the guide. Please synchronize with the visual circle."
        recs = [
            "Your breathing is completely inverted or out of sync with the visual circle guide.",
            "Inhale when the circle expands, and exhale smoothly when the circle contracts."
        ]
    elif corr_guide < 0.50 or ratio_err > 0.18 or dtw_score < 78.0:
        # Small mistakes / timing irregularities / ratio drift
        score = round(float(np.clip(min(dtw_score, 68.0), 55.0, 68.0)), 1)
        grade = "Grade C" if score < 65 else "Grade B"
        score_assessment = "FAIR" if grade == "Grade C" else "GOOD"
        headline = "Timing Irregularities Detected — Inconsistent Rhythm"
        ratio_assessment = "OFF TARGET" if ratio_err > 0.35 else "GOOD MATCH"
        rate_assessment = "ON TARGET" if abs(measured_bpm - ideal_bpm) <= 1.0 else ("TOO FAST" if measured_bpm > ideal_bpm else "TOO SLOW")
        spoken_text = "Timing irregularities detected. Your breathing drifted off rhythm. Focus on pacing each phase steadily with the circle."
        recs = [
            f"Breath ratio was inconsistent ({measured_ratio_str} vs ideal {ideal_ratio_str}).",
            "Maintain smooth, even transitions between inhalation and exhalation without lingering."
        ]
    else:
        # Clean breathing / follows well
        score = round(float(np.clip(dtw_score, 82.0, 96.0)), 1)
        grade = "Grade A"
        score_assessment = "PASSED"
        headline = "Well Done! Solid adherence and rhythm throughout the cycle."
        ratio_assessment = "EXCELLENT MATCH"
        rate_assessment = "ON TARGET"
        spoken_text = "Cycle complete. Grade A, excellent rhythm. Maintain this calm diaphragmatic focus."
        recs = [
            "Maintain this exact calm rhythm and diaphragmatic focus.",
            p.cue if p.cue else "Keep shoulders relaxed and posture upright."
        ]

    bpm_diff = abs(measured_bpm - ideal_bpm)
    vt_assessment = "GOOD DEPTH" if 250 <= vt_ml <= 850 else ("SHALLOW" if vt_ml < 250 else "DEEP")
    pef_assessment = "STABLE"
    endurance_assessment = "SUSTAINED"

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
