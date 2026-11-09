"""
Bhastrika (bellows breath, measured pace) - ideal (reference) 5-minute flow waveform + user comparison.

Sign convention: inhale = positive flow (L/s), exhale = negative flow (L/s).

Evidence level : Measured trace, ONE practitioner in 1-minute phases [IJPP]; slow variant from a protocol [B]
Notes          : 11.2 breaths/min, I:E 1:1.13, depth 2.5x baseline. Classical bellows is faster/forceful; this is the only measured trace found. Use --slow for 6/min, 4 s in / 6 s out.

No paper publishes an "ideal" flow-vs-time curve for this practice. Timing comes
from the references below; the curve SHAPE and any volume multiplier marked
"assumed" are modelling choices. Validate against a recorded trace from an
experienced practitioner on your own sensor.

References
  [IJPP] Breath characteristics in four volitional yoga breathing practices:
      https://ijpp.com/breath-characteristics-in-four-volitional-yoga-breathing-practices/
      (Bhastrika 11.21 breaths/min, I:E 1:1.13, amplitude 2.30 vs 0.93 baseline)
  [B] Immediate Effect of Slow Pace Bhastrika Pranayama on BP and HR:
      https://www.researchgate.net/publication/24144590 (6/min, 4 s in / 6 s out, 5 min)

SAFETY: breath holds and fast breathing can cause dizziness. Warn users with
hypertension, heart/lung disease or pregnancy to consult a doctor. Stop if
lightheaded.

Usage
    python bhastrika.py                       # 300 s reference -> CSV + PNG
    python bhastrika.py --tidal 0.6           # user's resting tidal volume (L)
    python bhastrika.py --user user.csv       # compare a recorded t,flow file
    python bhastrika.py --slow            # slow-pace variant (6/min)
"""
import argparse
import csv
import numpy as np

FS = 50.0                  # samples per second
DURATION_S = 300.0         # 5 minutes
NORMAL_TIDAL_L = 0.5       # resting tidal volume (L) - calibrate per user

NAME = "bhastrika"
TITLE = "Bhastrika (bellows breath, measured pace)"
# (phase, seconds, shape)   phase: in | hold | out    shape: sine | plateau
SEGS = [('in', 2.51, 'sine'), ('out', 2.84, 'sine')]
VOL_MULT = 2.5         # tidal-volume multiplier vs resting
# per-cycle (inhale_nostril, exhale_nostril), cycled; None = not nostril-specific
NOSTRIL = None


def shape_array(n, shape):
    x = (np.arange(n) + 0.5) / n
    if shape == "plateau":
        r = 0.15
        return np.sin(0.5 * np.pi * np.clip(np.minimum(x, 1 - x) / r, 0, 1))
    return np.sin(np.pi * x)


def build_cycle(segs, vol_l, fs):
    parts, phases = [], []
    for ph, sec, shape in segs:
        n = max(int(round(sec * fs)), 1)
        if ph == "hold":
            parts.append(np.zeros(n))
            phases += ["hold"] * n
        else:
            s = shape_array(n, shape)
            s = s * vol_l / (s.sum() / fs)           # area under curve = volume (L)
            parts.append(s if ph == "in" else -s)
            phases += ["inhale" if ph == "in" else "exhale"] * n
    return np.concatenate(parts), phases


def generate(segs, vol, duration=DURATION_S, fs=FS, tidal=NORMAL_TIDAL_L):
    cyc, ph = build_cycle(segs, tidal * vol, fs)
    need = int(duration * fs)
    flows, phases, nostrils, k = [], [], [], 0
    while sum(len(f) for f in flows) < need:
        flows.append(cyc)
        phases += ph
        if NOSTRIL:
            ni, ne = NOSTRIL[k % len(NOSTRIL)]
            lab = {"inhale": ni, "hold": "both", "exhale": ne}
            nostrils += [lab[x] for x in ph]
        else:
            nostrils += [""] * len(ph)
        k += 1
    flow = np.concatenate(flows)[:need]
    return np.arange(len(flow)) / fs, flow, phases[:need], nostrils[:need]


def spec(segs):
    """Target timing. Only a hold right after the inhale counts as 'hold'; a pause
    after the exhale (box breathing) adds to the period but is not compared."""
    d = {"in": 0.0, "hold": 0.0, "out": 0.0}
    prev = None
    for ph, sec, _ in segs:
        if ph == "hold":
            if prev == "in":
                d["hold"] += sec
        else:
            d[ph] += sec
        prev = ph
    d["period"] = sum(sec for _, sec, _ in segs)
    d["rate"] = 60.0 / d["period"]
    return d


# ------------------------- compare with a user's recording -------------------
def segment(t, flow, th_frac=0.1, min_run=0.3):
    fs = 1.0 / np.median(np.diff(t))
    # separate thresholds: exhale flow can be far weaker than inhale (e.g. Bhramari)
    th_in = th_frac * np.max(flow)
    th_out = th_frac * np.max(-flow)
    lab = np.where(flow > th_in, 1, np.where(flow < -th_out, -1, 0))
    runs, start = [], 0
    for i in range(1, len(lab) + 1):
        if i == len(lab) or lab[i] != lab[start]:
            runs.append([int(lab[start]), (i - start) / fs])
            start = i
    merged = []
    for r in runs:
        if merged and (r[1] < min_run or merged[-1][0] == r[0]):
            merged[-1][1] += r[1]
        else:
            merged.append(r)
    return merged


def analyse(t, flow):
    """Mean inhale / hold / exhale durations and breath period from a flow signal."""
    runs = segment(t, flow)
    ins, holds, outs, starts = [], [], [], []
    last = len(runs) - 1
    for i, (lab, d) in enumerate(runs):
        # skip the first run (may start mid-inhale) and any exhale that touches
        # the end of the recording (may be cut short)
        if lab == 1 and i > 0 and i + 1 < len(runs):
            nxt = runs[i + 1]
            h, e, j = 0.0, None, None
            if nxt[0] == 0 and i + 2 < len(runs) and runs[i + 2][0] == -1:
                h, e, j = nxt[1], runs[i + 2][1], i + 2
            elif nxt[0] == -1:
                e, j = nxt[1], i + 1
            if e is not None and j != last:
                ins.append(d); holds.append(h); outs.append(e)
                starts.append(sum(r[1] for r in runs[:i]))
    if len(ins) < 2:
        return None
    per = np.diff(starts)
    return {"in": float(np.mean(ins)), "hold": float(np.mean(holds)),
            "out": float(np.mean(outs)), "period": float(np.mean(per)),
            "rate": 60.0 / float(np.mean(per)),
            "period_cv": float(np.std(per) / np.mean(per)), "cycles": len(ins)}


def feedback(user, ref, tol=0.2):
    if user is None:
        return ["Not enough breath cycles detected - check the sensor and try again."]
    msgs = []
    names = {"in": "Inhale", "hold": "Breath hold", "out": "Exhale"}
    tips = {"in": ("lengthen the inhale", "shorten the inhale"),
            "hold": ("hold a little longer", "release the hold sooner"),
            "out": ("slow and lengthen the exhale", "you can exhale a bit faster")}
    for k in ("in", "hold", "out"):
        r, u = ref[k], user[k]
        if r == 0:
            if u > 0.5:
                msgs.append(f"No hold in this practice - you paused {u:.1f} s; flow straight into the exhale.")
            continue
        if u < (1 - tol) * r:
            msgs.append(f"{names[k]} too short ({u:.1f} s, target {r:.1f} s): {tips[k][0]}.")
        elif u > (1 + tol) * r:
            msgs.append(f"{names[k]} too long ({u:.1f} s, target {r:.1f} s): {tips[k][1]}.")
    if user["period_cv"] > 0.15:
        msgs.append("Breath cycles are uneven - keep every round the same length.")
    if not msgs:
        msgs.append("Good - your pattern matches the reference timing.")
    return msgs


# ---------------------------------- output -----------------------------------
def save_csv(path, t, flow, phases, nostrils):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t_s", "flow_L_per_s", "phase", "nostril"])
        for row in zip(t, flow, phases, nostrils):
            w.writerow([f"{row[0]:.3f}", f"{row[1]:.5f}", row[2], row[3]])


def plot(t, flow, segs, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(2, 1, figsize=(11, 6))
    ax[0].plot(t, flow, lw=0.6)
    ax[0].set(title=TITLE + " - reference flow, full 5 minutes",
              xlabel="time (s)", ylabel="flow (L/s)\n(+inhale / -exhale)")
    zoom = min(3 * spec(segs)["period"], t[-1])
    m = t <= zoom
    ax[1].plot(t[m], flow[m])
    ax[1].set(title="First cycles (zoom)", xlabel="time (s)", ylabel="flow (L/s)")
    for a in ax:
        a.axhline(0, color="gray", lw=0.4)
        a.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=130)


def main():
    ap = argparse.ArgumentParser(description=TITLE)
    ap.add_argument("--duration", type=float, default=DURATION_S)
    ap.add_argument("--tidal", type=float, default=NORMAL_TIDAL_L,
                    help="user's resting tidal volume in litres")
    ap.add_argument("--out", default=".", help="output folder")
    ap.add_argument("--user", help="CSV with columns t,flow to compare")
    ap.add_argument("--no-plot", action="store_true")
    ap.add_argument("--slow", action="store_true",
                    help="slow-pace variant: 6/min, 4 s in / 6 s out [B]; volume assumed 1.5x")
    a = ap.parse_args()

    segs, vol = list(SEGS), VOL_MULT
    if a.slow:
        segs, vol = [("in", 4.0, "sine"), ("out", 6.0, "sine")], 1.5

    t, flow, ph, ns = generate(segs, vol, a.duration, FS, a.tidal)
    s = spec(segs)
    base = f"{a.out}/{NAME}_reference"
    save_csv(base + ".csv", t, flow, ph, ns)
    print(f"{TITLE}: {len(t)} samples, {s['rate']:.1f} breaths/min "
          f"(in {s['in']:.1f}s, hold {s['hold']:.1f}s, out {s['out']:.1f}s), "
          f"peak in {flow.max():.2f} L/s, peak out {-flow.min():.2f} L/s")
    print(f"Saved {base}.csv")
    if not a.no_plot:
        plot(t, flow, segs, base + ".png")
        print(f"Saved {base}.png")

    if a.user:
        d = np.loadtxt(a.user, delimiter=",", skiprows=1)
        usr = analyse(d[:, 0], d[:, 1])
        print("Reference:", {k: round(v, 2) for k, v in s.items()})
        print("User     :", usr and {k: round(v, 2) for k, v in usr.items()})
        for m in feedback(usr, s):
            print(" -", m)


if __name__ == "__main__":
    main()
