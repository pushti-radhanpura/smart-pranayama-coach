"""
Kapalabhati pranayama - 5-minute ideal (reference) flow waveform + user comparison.

Sign convention: inhale = positive flow (L/s), exhale = negative flow (L/s).

Parameters are built from the published sources below. The exhale width and the
passive-inhale shape are MODELLING CHOICES (no paper publishes a Kapalabhati
flow-vs-time curve) - validate them against a recorded trace from an
experienced practitioner on your own sensor.

Sources
 [1] Gamma-oscillation study, 1 Hz (60 strokes/min), 5-min sessions
     https://pmc.ncbi.nlm.nih.gov/articles/PMC11495304/
 [2] JIPMER 6-week training (30 strokes/1 min -> 5 min/session)
     https://www.saspublishers.com/article/8939/download/
 [3] Some Respiratory Studies in Respect of Kapalabhati ... (tidal volume 2.6x
     smaller than normal breathing)
     https://www.researchgate.net/publication/346492574
 [4] Breath characteristics in four volitional yoga breathing practices (IJPP)
     https://ijpp.com/breath-characteristics-in-four-volitional-yoga-breathing-practices/
 [5] Kapalabhati pranayama (PMC4959327) - O2 consumption 1.1-1.8x resting
 [6] Influence of kapalabhati on SpO2 and BP - 60-120 breaths/min
     https://www.researchgate.net/publication/344014436
 [7] Kapalabhati and pneumothorax case report (safety)

SAFETY: warn users with hypertension, heart/lung disease or pregnancy to consult
a doctor first. Stop on dizziness.

Usage
    python kapalabhati_waveform.py                  # 5 min, 60 strokes/min
    python kapalabhati_waveform.py --rate 90        # faster
    python kapalabhati_waveform.py --beginner       # 30 -> 60 strokes/min ramp
    python kapalabhati_waveform.py --user user.csv  # compare a recorded flow (t,flow)
"""
import argparse
import numpy as np

# ----------------------------- defaults ------------------------------------
DURATION_S = 300.0          # 5 minutes [1][2]
FS = 50.0                   # samples per second
RATE_SPM = 60.0             # strokes/min; 60-120 range [1][6]
EXHALE_S = 0.25             # forceful exhale burst (modelling choice)
NORMAL_TIDAL_L = 0.5        # user's normal tidal volume (L)
TIDAL_RATIO = 1 / 2.6       # Kapalabhati tidal volume vs normal [3]
REST_AT_S = 150.0           # optional one-minute break between two 150 s sets [1]
REST_LEN_S = 60.0


def stroke_pulse(period, exhale_s, vol_exh, fs):
    """One stroke: half-sine exhale burst (negative) + passive half-sine inhale
    (positive) of equal volume so that net volume per cycle = 0."""
    n = int(round(period * fs))
    t = np.arange(n) / fs
    flow = np.zeros(n)
    te = min(exhale_s, 0.45 * period)
    ti = period - te
    # half-sine integral = peak * 2*T/pi  ->  peak = V*pi/(2*T)
    pk_e = vol_exh * np.pi / (2 * te)
    pk_i = vol_exh * np.pi / (2 * ti)
    m_e = t < te
    flow[m_e] = -pk_e * np.sin(np.pi * t[m_e] / te)
    m_i = ~m_e
    flow[m_i] = pk_i * np.sin(np.pi * (t[m_i] - te) / ti)
    return flow


def rate_at(t, rate, beginner):
    """Strokes/min at time t. Beginner: ramp 30 -> rate over first 60 s [2]."""
    if beginner:
        return 30.0 + (rate - 30.0) * min(t / 60.0, 1.0)
    return rate


def generate(duration=DURATION_S, fs=FS, rate=RATE_SPM, exhale_s=EXHALE_S,
             normal_tidal=NORMAL_TIDAL_L, beginner=False, with_rest=False):
    vol = normal_tidal * TIDAL_RATIO
    chunks, t_now = [], 0.0
    rested = False
    while t_now < duration:
        if with_rest and not rested and t_now >= REST_AT_S:
            chunks.append(np.zeros(int(REST_LEN_S * fs)))
            t_now += REST_LEN_S
            rested = True
            continue
        period = 60.0 / rate_at(t_now, rate, beginner)
        cyc = stroke_pulse(period, exhale_s, vol, fs)
        chunks.append(cyc)
        t_now += len(cyc) / fs
    flow = np.concatenate(chunks)[: int(duration * fs)]
    t = np.arange(len(flow)) / fs
    return t, flow


# ----------------------- compare with user's recording ----------------------
def analyse(t, flow):
    """Extract per-stroke features from a flow signal (exhale = negative)."""
    from scipy.signal import find_peaks
    fs = 1.0 / np.median(np.diff(t))
    # exhale peaks = minima -> peaks of -flow
    thr = 0.3 * np.max(-flow)
    pk, _ = find_peaks(-flow, height=thr, distance=int(0.2 * fs))
    if len(pk) < 3:
        return None
    periods = np.diff(t[pk])
    rate = 60.0 / np.mean(periods)
    peak_exh = np.mean(-flow[pk])
    # exhale duration: time flow stays below 10% of peak around each peak
    widths = []
    for p in pk:
        lvl = 0.1 * flow[p]  # 10% of the (negative) exhale peak
        a = p
        while a > 0 and flow[a] < lvl:
            a -= 1
        b = p
        while b < len(flow) - 1 and flow[b] < lvl:
            b += 1
        widths.append((b - a) / fs)
    return dict(rate=rate, rate_cv=np.std(periods) / np.mean(periods),
                peak_exhale=peak_exh, exhale_s=float(np.mean(widths)),
                strokes=len(pk), duration=t[-1] - t[0])


def feedback(user, ref):
    """Turn feature differences into plain-language instructions."""
    msgs = []
    if user is None:
        return ["Not enough strokes detected - check the sensor and start again."]
    dr = user["rate"] - ref["rate"]
    if dr > 10:
        msgs.append(f"Too fast ({user['rate']:.0f}/min). Slow to ~{ref['rate']:.0f}/min.")
    elif dr < -10:
        msgs.append(f"Too slow ({user['rate']:.0f}/min). Speed up to ~{ref['rate']:.0f}/min.")
    if user["rate_cv"] > 0.15:
        msgs.append("Rhythm is uneven - keep every stroke the same length.")
    if user["peak_exhale"] < 0.6 * ref["peak_exhale"]:
        msgs.append("Exhale too weak - snap the belly in more sharply.")
    elif user["peak_exhale"] > 1.6 * ref["peak_exhale"]:
        msgs.append("Exhale too forceful - ease off to avoid dizziness.")
    if user["exhale_s"] > 1.6 * ref["exhale_s"]:
        msgs.append("Exhale too long - make it short and sharp, let the inhale be passive.")
    if not msgs:
        msgs.append("Good - your Kapalabhati matches the reference pattern.")
    return msgs


def dtw_distance(a, b):
    """Plain DTW on short, normalised snippets (O(n*m))."""
    a = (a - a.mean()) / (a.std() + 1e-9)
    b = (b - b.mean()) / (b.std() + 1e-9)
    D = np.full((len(a) + 1, len(b) + 1), np.inf)
    D[0, 0] = 0
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            c = abs(a[i - 1] - b[j - 1])
            D[i, j] = c + min(D[i - 1, j], D[i, j - 1], D[i - 1, j - 1])
    return D[-1, -1] / (len(a) + len(b))


# --------------------------------- main -------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rate", type=float, default=RATE_SPM)
    ap.add_argument("--duration", type=float, default=DURATION_S)
    ap.add_argument("--exhale", type=float, default=EXHALE_S)
    ap.add_argument("--tidal", type=float, default=NORMAL_TIDAL_L)
    ap.add_argument("--beginner", action="store_true")
    ap.add_argument("--rest", action="store_true", help="1-min break at 150 s")
    ap.add_argument("--user", help="CSV with columns t,flow to compare")
    ap.add_argument("--no-plot", action="store_true")
    a = ap.parse_args()

    t, flow = generate(a.duration, FS, a.rate, a.exhale, a.tidal, a.beginner, a.rest)
    np.savetxt("kapalabhati_reference.csv", np.c_[t, flow], delimiter=",",
               header="t_s,flow_L_per_s", comments="")
    print(f"Saved kapalabhati_reference.csv ({len(t)} samples, {t[-1]:.0f} s)")

    if not a.no_plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(2, 1, figsize=(11, 6))
        ax[0].plot(t, flow, lw=0.4)
        ax[0].set(title="Kapalabhati reference flow - full session",
                  xlabel="time (s)", ylabel="flow (L/s)\n(+inhale / -exhale)")
        m = t < 6
        ax[1].plot(t[m], flow[m])
        ax[1].set(title="First 6 s (zoom)", xlabel="time (s)", ylabel="flow (L/s)")
        for x in ax:
            x.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig("kapalabhati_reference.png", dpi=130)
        print("Saved kapalabhati_reference.png")

    if a.user:
        d = np.loadtxt(a.user, delimiter=",", skiprows=1)
        ut, uf = d[:, 0], d[:, 1]
        ref_feat = analyse(t, flow)
        usr_feat = analyse(ut, uf)
        print("Reference:", ref_feat)
        print("User     :", usr_feat)
        # DTW on first 10 s of each, resampled to the same rate
        n = int(10 * FS)
        ur = np.interp(np.arange(n) / FS, ut - ut[0], uf)
        print(f"DTW distance (first 10 s, lower = closer): {dtw_distance(flow[:n], ur):.3f}")
        for m in feedback(usr_feat, ref_feat):
            print(" -", m)


if __name__ == "__main__":
    main()
