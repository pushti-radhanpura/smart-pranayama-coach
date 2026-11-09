"""
pranayama_coach.py
==================
Turns the DTW comparison into a live, polite, spoken review.

Three possible outcomes while the user practises:
    "excellent"  - matches the ideal well            -> praise, keep going
    "good_minor" - small mistakes                    -> encourage + specific gentle tip, keep going
    "stop"       - completely out of phase / wrong   -> politely ask to stop and restart

Your website calls  LiveCoach.evaluate(times, values)  every few seconds with the
signal collected so far.  It returns a Verdict; if verdict.speak is True the
frontend should read verdict.message aloud (see the browser snippet in the chat).

Demo (no hardware):   python pranayama_coach.py
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

import pranayama_dtw as dtw

# ---------------------------------------------------------------- thresholds
EXCELLENT_SCORE = 78      # mean cycle score >= this (and no tips)  -> excellent
STOP_SCORE = 45           # mean cycle score  < this                -> severe
STOP_CORR = 0.25          # shape correlation < this (inverted / unrelated) -> severe
SYNC_STOP_CORR = 0.40     # correlation with the guide's clock < this -> out of phase
TEMPO_RANGE = (0.5, 2.0)  # pace more than 2x off                   -> severe
TIP_LOW, TIP_HIGH = 0.75, 1.25   # a phase <75% or >125% of ideal triggers a tip

PHASE_WORDS = {
    "inhale": "inhalation",
    "hold_in": "breath hold after inhaling",
    "exhale": "exhalation",
    "hold_out": "pause after exhaling",
}


@dataclass
class Verdict:
    status: str                 # warming_up | excellent | good_minor | drifting | stop
    message: str                # polite English sentence(s) to display AND speak
    speak: bool                 # should the frontend say it aloud right now?
    stop_session: bool = False  # frontend should halt the exercise
    score: float = 0.0
    details: dict = field(default_factory=dict)


class LiveCoach:
    def __init__(self, pattern: dtw.Pattern, fs: float = 10.0, window_cycles: int = 3,
                 stop_after: int = 2, speak_cooldown: dict | None = None,
                 guided: bool = True):
        self.p, self.fs = pattern, fs
        # guided=True : the user follows an on-screen/audio guide that started at t = t[0]
        # guided=False: self-paced, so only the SHAPE of each cycle is judged
        self.guided = guided
        self.window_cycles = window_cycles
        self.stop_after = stop_after          # consecutive severe checks before stopping
        self.cooldown = speak_cooldown or {"excellent": 60, "good_minor": 30, "drifting": 20}
        self._bad_streak = 0
        self._last_status, self._last_spoken_at = None, -1e9
        self._ideal, self._bounds = dtw.aligned_cycle(pattern, fs)

    # ------------------------------------------------------------------ core
    def evaluate(self, t: np.ndarray, raw: np.ndarray, now: float | None = None) -> Verdict:
        """t, raw : everything captured so far (seconds, signal value)."""
        now = time.time() if now is None else now
        t, raw = np.asarray(t, float), np.asarray(raw, float)
        elapsed = t[-1] - t[0]
        cyc_s = self.p.cycle_s

        if elapsed < max(1.5 * cyc_s, 8.0):
            return Verdict("warming_up", "", False)

        window_s = max(30.0, (self.window_cycles + 1) * cyc_s)
        m = t >= t[-1] - window_s
        x = dtw.preprocess(t[m], raw[m], self.fs)
        spans = dtw.segment_cycles(x, self.fs, 0.5 * cyc_s)[-self.window_cycles:]

        if not spans:
            # cannot even find breath cycles
            severe = elapsed >= 2 * cyc_s
            return self._finish(severe, 0.0, {"reason": "no_cycles"}, [], now)

        results, corrs = [], []
        ref = dtw.znorm(self._ideal)
        for k, (a, b) in enumerate(spans):
            c = x[a:b]
            results.append(dtw.analyse_cycle(k, a / self.fs, c, self._ideal, self._bounds,
                                             self.p, self.fs, 0.15))
            u = np.interp(np.linspace(0, len(c) - 1, len(ref)), np.arange(len(c)), c)
            corrs.append(float(np.corrcoef(dtw.znorm(u), ref)[0, 1]))

        score = float(np.mean([r.score for r in results]))
        corr = float(np.mean(corrs))
        tempo = float(np.median([r.tempo_ratio for r in results]))
        severe = score < STOP_SCORE or corr < STOP_CORR or not (TEMPO_RANGE[0] <= tempo <= TEMPO_RANGE[1])

        sync_corr, lag = None, 0.0
        if self.guided and cyc_s >= 4:          # not meaningful for very fast patterns
            sync_corr, lag = self._guide_sync(x, float(t[m][0]), float(t[0]))
            if sync_corr < SYNC_STOP_CORR:
                severe = True                   # completely out of phase with the guide
        tips = [] if severe else self._tips(results, tempo)
        if not severe and sync_corr is not None and abs(lag) > 0.75 * self._lag_limit:
            side = "behind" if lag > 0 else "ahead of"
            tips.append(f"try to begin each breath together with the guide, as you are slightly {side} it")
            tips = tips[:2]
        return self._finish(severe, score, {"corr": corr, "tempo": tempo,
                                            "sync_corr": sync_corr, "lag_s": lag}, tips, now)

    def _guide_sync(self, x: np.ndarray, t_start: float, t0: float):
        """Compare the recent window with the guide's own clock.
        Returns (best_corr, lag_seconds). lag > 0 means the user is BEHIND the guide.
        This is what catches 'breathing the opposite way', which a per-cycle
        comparison alone cannot see (each cycle is re-aligned to its own start)."""
        cyc = dtw.one_cycle(self.p, self.fs)
        idx = (int(round((t_start - t0) * self.fs)) + np.arange(len(x))) % len(cyc)
        g = dtw.znorm(cyc[idx])
        self._lag_limit = max(1.0, min(0.12 * self.p.cycle_s, 2.5))   # tolerance (s); never below normal human reaction time
        L = int(self._lag_limit * self.fs)
        best, best_k = -2.0, 0
        for k in range(-L, L + 1):
            a, b = (x[k:], g[:len(g) - k]) if k >= 0 else (x[:k], g[-k:])
            if len(a) < 20 or a.std() < 1e-9 or b.std() < 1e-9:
                continue
            c = float(np.corrcoef(a, b)[0, 1])
            if c > best:
                best, best_k = c, k
        return best, best_k / self.fs

    # ------------------------------------------------------------- messaging
    def _tips(self, results, tempo) -> list[str]:
        tips = []
        if tempo > 1.2:
            tips.append("breathe a little faster to match the guide")
        elif tempo < 0.83:
            tips.append("breathe a little slower to match the guide")
        if self.p.cycle_s >= 3:  # phase timing is unreliable for very fast patterns
            for ph, word in PHASE_WORDS.items():
                ideal = getattr(self.p, ph)
                if ideal < 1:
                    continue
                med = float(np.median([r.phases["user"][ph] for r in results]))
                if med < TIP_LOW * ideal:
                    tips.append(f"lengthen your {word} slightly, to about {ideal:g} seconds")
                elif med > TIP_HIGH * ideal:
                    tips.append(f"shorten your {word} slightly, to about {ideal:g} seconds")
        return tips[:2]   # never overwhelm someone with their eyes closed

    def _finish(self, severe, score, details, tips, now) -> Verdict:
        name = self.p.name
        if severe:
            self._bad_streak += 1
            if self._bad_streak >= self.stop_after:
                msg = (f"I am sorry to interrupt. Your breathing is quite different from the "
                       f"{name} pattern, so let us pause here. Please open your eyes gently, "
                       f"relax for a moment, and then begin again by following the guide from the start.")
                return Verdict("stop", msg, True, True, score, details)
            msg = ("Your breathing seems to be drifting away from the guide. "
                   "Please try to follow the rhythm of the guide more closely.")
            return self._with_cooldown("drifting", msg, score, details, now)

        self._bad_streak = 0
        if score >= EXCELLENT_SCORE and not tips:
            msg = f"Wonderful! You are following the {name} beautifully. Please continue in the same calm rhythm."
            return self._with_cooldown("excellent", msg, score, details, now)

        if tips:
            msg = ("You are doing well. With a small correction your breathing will be even better: "
                   + " and ".join(tips) + ". " + (self.p.cue or "") + " Please continue.")
        else:
            msg = ("You are doing well. Please try to keep your rhythm a little steadier. "
                   + (self.p.cue or "") + " Please continue.")
        return self._with_cooldown("good_minor", msg.replace("  ", " ").strip(), score, details, now)

    def _with_cooldown(self, status, msg, score, details, now) -> Verdict:
        changed = status != self._last_status
        due = now - self._last_spoken_at >= self.cooldown.get(status, 30)
        speak = changed or due
        if speak:
            self._last_status, self._last_spoken_at = status, now
        return Verdict(status, msg, speak, False, score, details)

    # ------------------------------------------------------- end of session
    def final_review(self, t, raw) -> str:
        x = dtw.preprocess(np.asarray(t, float), np.asarray(raw, float), self.fs)
        res = dtw.analyse_session(x, self.p, self.fs)
        S = res["summary"]
        s = 0.7 * S["mean_cycle_score"] + 0.3 * S["session_score"]
        tips = self._tips(res["cycles"], S["median_tempo_ratio"]) if res["cycles"] else []
        if s >= 80 and not tips:
            head = "Congratulations! Your practice was excellent."
        elif s >= 60:
            head = "Well done. You completed the practice and did good work."
        else:
            head = "Thank you for practising. This pattern needs a little more practice, which is perfectly normal."
        extra = (" For next time, please " + " and ".join(tips) + ".") if tips else ""
        return f"{head} You completed about {S['n_cycles_found']} breath cycles of {self.p.name}.{extra}"


# --------------------------------------------------------------------------
# Optional offline speech for the desktop demo only (pip install pyttsx3).
# On the website use the browser's built-in speechSynthesis instead.
# --------------------------------------------------------------------------
def speak_local(text: str) -> None:
    try:
        import pyttsx3
    except ImportError:
        print("      (no voice: run  pip install pyttsx3  and try again)")
        return
    try:
        eng = pyttsx3.init()
        eng.setProperty("rate", 150)       # words per minute - lower is calmer
        eng.say(text)
        eng.runAndWait()
    except Exception as e:                 # e.g. no audio device
        print(f"      (voice failed: {e})")


# --------------------------------------------------------------------------
# Demo: simulate three kinds of users and watch the coach react in "real time"
# --------------------------------------------------------------------------
def _demo(key="sama_vritti", voice=False):
    p = dtw.PATTERNS[key]
    fs = 10.0
    scenarios = {}

    kw = dict(fs=fs, duration_s=240, tempo_drift=0, sloppiness=0.05, noise=0.06,
              lapse_cycle=-1, resync=0.02, lag_s=0.8, guide_cycle=p.cycle_s)
    # 1) follows the guide well
    scenarios["Good practice"] = dtw.simulate_user(p, seed=1, **kw)
    # 2) holds the breath too briefly after inhaling and waits too long after exhaling
    q = dtw.Pattern("u", p.inhale, p.hold_in * 0.5, p.exhale, p.hold_out + p.hold_in * 0.5)
    scenarios["Small mistakes (short hold)"] = dtw.simulate_user(q, seed=2, **{**kw, "resync": 0.02})
    # 3) completely out of phase: inhaling while the guide says exhale
    t, x = dtw.simulate_user(p, seed=3, **kw)
    shift = int(p.cycle_s / 2 * fs)
    scenarios["Completely out of phase (half a cycle off)"] = (t, np.roll(x, shift))
    # 4) breathing a totally different, random rhythm
    rng = np.random.default_rng(4)
    t = np.arange(0, 240, 1 / fs)
    scenarios["Random / unrelated breathing"] = (t, np.convolve(rng.normal(size=len(t)), np.ones(15) / 15, "same"))

    for title, (t, x) in scenarios.items():
        print("\n" + "=" * 70 + f"\n{title}   [{p.name}]\n" + "=" * 70)
        coach, clock = LiveCoach(p, fs), 0.0
        for check in np.arange(20, 241, 20):             # website checks every 20 s
            m = t <= check
            v = coach.evaluate(t[m], x[m], now=float(check))
            if v.status == "warming_up":
                continue
            flag = "SPEAK" if v.speak else "     "
            print(f"[{check:3.0f}s] {v.status:10s} score={v.score:5.1f} {flag} | {v.message}")
            if v.speak and voice:
                speak_local(v.message)
            if v.stop_session:
                print("        >>> SESSION STOPPED by coach <<<")
                break
        else:
            print("\nFINAL:", coach.final_review(t, x))


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--pattern", default="sama_vritti", choices=dtw.PATTERNS)
    ap.add_argument("--voice", action="store_true", help="speak aloud (needs pip install pyttsx3)")
    a = ap.parse_args()
    _demo(a.pattern, a.voice)
