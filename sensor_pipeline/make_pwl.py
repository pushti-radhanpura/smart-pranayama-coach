"""
STEP 1 - Stimulus for LTspice.
Creates the capacitance-vs-time file (cap_pwl.txt) that drives the sensor in LTspice,
plus a ready-to-run netlist (breath_sensor.cir).

This is the ONLY place where a breathing pattern is chosen (it plays the role of the person).
The analysis script (analyze_ltspice.py) never sees these numbers - it only reads LTspice's output.

TIME_SCALE: LTspice would need ~100 s x 10 kHz = millions of steps for a real-time breath,
so the breath is played TIME_SCALE times faster. The analyzer undoes this.
"""
import numpy as np, json

# ---- sensor (same as cap_model.py / LTspice) ----
EPS0, EPS_R = 8.854e-12, 3.0
WIDTH, PLATE_LEN, OVERLAP0, GAP = 20e-3, 60e-3, 40e-3, 1e-3
ETA = 1.0

# ---- the breathing the "person" performs ----
INHALE, HOLD_IN, EXHALE, HOLD_OUT = 4.0, 2.0, 6.0, 0.0   # seconds (real time)
N_BREATHS = 12
MAX_EXP_MM = 10.0
FATIGUE_START_S, FATIGUE_PER_BREATH = 48.0, 0.08          # demo of tiring

TIME_SCALE = 100          # LTspice runs 100x faster than real breathing
FS_WAVE = 100.0           # real-time sampling of the PWL data (Hz)

segs, t0, n_late = [], 0.0, 0
for _ in range(N_BREATHS):
    if t0 >= FATIGUE_START_S:
        n_late += 1
    s = (1 - FATIGUE_PER_BREATH) ** n_late
    segs.append((t0, INHALE*s, HOLD_IN*s, EXHALE*s, MAX_EXP_MM*(1-0.5*FATIGUE_PER_BREATH)**n_late))
    t0 += (INHALE + HOLD_IN + EXHALE + HOLD_OUT) * s
T_END = t0 + 2.0                                           # 2 s of rest at the end

t = np.arange(0, T_END, 1/FS_WAVE)
exp_mm = np.zeros_like(t)
for (a, ti, th, te, amp) in segs:
    p = t - a
    m = (p >= 0) & (p < ti);            exp_mm[m] = amp/2*(1-np.cos(np.pi*p[m]/ti))
    m = (p >= ti) & (p < ti+th);        exp_mm[m] = amp
    m = (p >= ti+th) & (p < ti+th+te);  exp_mm[m] = amp/2*(1+np.cos(np.pi*(p[m]-ti-th)/te))

overlap = np.clip(OVERLAP0 - ETA*exp_mm*1e-3, 0, PLATE_LEN)
C_pF = EPS0*EPS_R*WIDTH*overlap/GAP*1e12

t_sim = t / TIME_SCALE
np.savetxt("cap_pwl.txt", np.column_stack((t_sim, C_pF)), fmt=["%.9e", "%.6f"], delimiter="\t")
tstop = t_sim[-1]
json.dump({"time_scale": TIME_SCALE, "tstop": tstop}, open("pwl_meta.json", "w"))

open("breath_sensor.cir", "w").write(f"""* Capacitive breath sensor - RC divider with time-varying capacitor
V1 in 0 SINE(0 5 10000)
R1 in out 1Meg
* V2 carries the capacitance in pF as a plain number
V2 cap 0 PWL file=cap_pwl.txt
* B1 behaves as a capacitor C(t): i = d(C*v)/dt
B1 out 0 I=ddt(V(cap)*1e-12*V(out))
.tran 0 {tstop:.6f} 0 2u
.save V(out)
.end
""")
print(f"cap_pwl.txt written ({len(t)} points), simulated time = {tstop:.4f} s")
print(f"LTspice: run breath_sensor.cir, then File > Export data as text > V(out) -> ltspice_export.txt")
