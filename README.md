# Smart Pranayama Breathing Coach

A full-stack, research-informed breathing coach and sensor analysis system that merges:
1. **Interactive Breathing Guide (Frontend)** — Vanilla single-page application with vintage-minimal design, dynamic breathing circle animation, and live dual-trace waveform visualization.
2. **Real-time Live DTW Coach (Backend)** — Band-constrained Sakoe-Chiba Dynamic Time Warping (DTW) evaluating respiratory shape and tempo, providing live polite verbal corrections and cycle scores.
3. **Biomedical Capacitive Breath Sensor Pipeline** — Signal recovery for chest-belt capacitive sensors simulated in LTspice, demodulating the 10 kHz carrier to extract tidal volume, flow rates, respiratory rate (BPM), and pattern endurance.

---

## Target Folder Structure

```
smart_pranayama_coach/
  backend/
    server.py             # FastAPI production server with static mounting & endpoints
    coach_service.py      # Session management, validation, DTW evaluate/finish/compare
    pranayama_coach.py    # Live coaching engine, thresholds, polite verbal phrasing
    pranayama_dtw.py      # Core Sakoe-Chiba banded DTW, preprocessing, scoring
    patterns.json         # 10 standardized pranayama patterns with curve links
    sensor_service.py     # Glue service importing and caching sensor pipeline results
    requirements.txt      # Python dependencies
    curves/               # Flattened directory of 10 reference curves & recipes:
      anuloma_viloma.py, bhastrika.py, bhastrika_reference.csv, bhastrika_reference.png,
      bhramari.py, kapalabhati.csv, kapalabhati_waveform.py, nadi_shodhana.py,
      nadi_shodhana_reference.csv, nadi_shodhana_reference.png, sama_vritti.py,
      sheetali.py, sheetkari.py, surya_bhedana.py, ujjayi.py
  sensor_pipeline/
    cap_model.py          # Physics model: plate overlap, capacitance, divider voltage
    analyze_ltspice.py    # Carrier demodulation, signal recovery, 5-parameter extraction
    breath_sensor.cir     # LTspice netlist for time-varying capacitor divider
    breath_wave_from_ltspice.csv # Demodulated benchmark breathing wave
    ltspice_export.txt    # 22.6 MB exported simulation carrier data
    make_pwl.py           # Stimulus generator creating cap_pwl.txt and breath_sensor.cir
    cap_pwl.txt           # PWL piecewise linear capacitance stimulus file
    pwl_meta.json         # Time-scale metadata (default 100x acceleration)
  frontend/
    index.html            # SPA entry point with responsive semantic markup
    style.css             # Vintage-minimal stylesheet with dynamic circle transitions
    app.js                # Formatted client router, live coach loop, sensor dashboard
    coach_client.js       # PranayamaCoach.CoachClient class with SpeechSynthesis
  README.md               # Complete documentation
```

---

## 🚀 Quick Deploy: How to Get a Live Shareable Link

You can deploy this full-stack application and obtain a live public URL in under 3 minutes using free cloud providers.

### Option 1: Deploy to Render.com (Recommended — 100% Free & 1-Click)
Render runs the FastAPI backend and serves the frontend together under HTTPS:
1. Create a repository on GitHub (e.g. `smart-pranayama-coach`) and push your code:
   ```bash
   git init
   git add .
   git commit -m "Initial commit of Smart Pranayama Coach"
   git branch -M main
   git remote add origin https://github.com/<your-username>/smart-pranayama-coach.git
   git push -u origin main
   ```
2. Log into [Render.com](https://render.com) and click **New > Web Service**.
3. Select your GitHub repository.
4. Render automatically detects `render.yaml` (or choose **Python**, Build command: `pip install -r backend/requirements.txt`, Start command: `python main.py`).
5. Click **Create Web Service**. Within ~2 minutes, your live link `https://smart-pranayama-coach.onrender.com` will be active!

### Option 2: Deploy to Hugging Face Spaces (Free Forever, No Card Required)
1. Go to [Hugging Face Spaces](https://huggingface.co/spaces) and click **Create new Space**.
2. Space SDK: Select **Docker** (Blank).
3. Clone your Space repo and push these project files (including the bundled `Dockerfile`).
4. Hugging Face builds the Docker container automatically and gives you an instant public URL (e.g. `https://huggingface.co/spaces/<user>/smart-pranayama-coach`).

### Option 3: Deploy to Railway / Fly.io / Koyeb
- **Railway**: Click **New Project > Deploy from GitHub repo**. Railway reads `Procfile` or `Dockerfile` and deploys automatically.
- **Fly.io**: Run `fly launch` in the project root; it detects `Dockerfile` and deploys globally.

### Option 4: Split Hosting (Frontend on GitHub Pages + Backend on Cloud)
If you prefer hosting the static frontend directly on GitHub Pages (`https://<user>.github.io/<repo>`):
1. Deploy your backend on Render / Railway to get your API URL (e.g. `https://my-backend.onrender.com`).
2. In `frontend/index.html` (inside `<head>`), add this one line:
   ```html
   <script>window.COACH_API_BASE = "https://my-backend.onrender.com";</script>
   ```
3. Enable GitHub Pages in your repo (**Settings > Pages > Branch: main > /frontend** folder).

---

## 1. Prerequisites & Installation

- **Python**: 3.10 or newer (tested on Python 3.10 – 3.14)
- **Node.js**: v18+ (optional, only for frontend syntax checks & browser testing)
- **Modern Web Browser**: Chrome, Edge, Firefox, or Safari (SpeechSynthesis supported)

### Setting up Python Virtual Environment

```bash
cd smart_pranayama_coach
python3 -m venv venv
source venv/bin/activate       # On Windows: venv\Scripts\activate

# Install backend dependencies
pip install -r backend/requirements.txt
```

---

## 2. Running the Application

### Option A: Running from inside `backend/`

```bash
cd backend
uvicorn server:app --reload
```

### Option B: Running directly with Python

```bash
cd backend
python server.py
```

### Option C: Running from any directory (arbitrary working directory)

The server resolves all relative paths (`patterns.json`, `curves/`, `frontend/`, `sensor_pipeline/`) dynamically using file-relative paths:

```bash
cd /
uvicorn --app-dir /path/to/smart_pranayama_coach/backend server:app --reload --port 8000
```

Open your browser and navigate to:
```
http://127.0.0.1:8000
```

---

## 3. How to Use the Live Coach

1. **Select a Practice**: From the homepage or navigation bar, click **Pranayamas** and choose any of the 10 techniques (e.g. *Sama Vritti*, *Nadi Shodhana*, *Ujjayi*, *Kapalabhati*).
2. **Review Safety Notes**: Read the traditional method, benefits, and safety contraindications.
3. **Configure Session**:
   - **Input Source**:
     - *Live coach - Simulated (follows well)*: Synthetic user following the guide with small natural human variance.
     - *Live coach - Simulated (small mistakes)*: Shortened breath holds triggering gentle coaching tips.
     - *Live coach - Simulated (out of phase)*: Inverted breathing (180° shifted) triggering the polite out-of-phase stop safety mechanism.
     - *Live coach - LTspice sensor replay*: Replays calibrated sensor samples from `breath_wave_from_ltspice.csv` into the coach.
     - *Reference only*: Self-paced visual timer without verbal coaching.
   - **Spoken Coach Voice**: Toggle on/off for browser `SpeechSynthesis` audio feedback (defaults to `"en-IN"` voice with fallback to any English voice).
4. **Click Start**:
   - The user gesture unlocks audio playback in the browser.
   - The animated breathing circle scales smoothly according to the phase length (dynamic transition duration).
   - Real-time signals are plotted on the canvas (dashed ideal template vs solid live user curve).
5. **Session Review & DTW Alignment**:
   - Click **End & Review** (or complete 5 minutes) to receive final feedback and cycle counts.
   - Click **🔍 Compare with Ideal (DTW)** to view the Sakoe-Chiba aligned waveforms, DTW distance, session score, and cycle-by-cycle score breakdown.

---

## 4. Sakoe-Chiba Banded DTW Implementation

Sakoe-Chiba constrained Dynamic Time Warping lives in `backend/pranayama_dtw.py`:

- **Band-Constrained Matrix (`_dtw_matrix`)**:
  Computes accumulated cost matrix $D(i, j) = |x_{i-1} - y_{j-1}| + \min(D_{i-1,j}, D_{i,j-1}, D_{i-1,j-1})$ exclusively within a diagonal band $|j - i \cdot \text{slope}| \le \text{radius}$.
- **Traceback (`_traceback`)**:
  Traces the optimal alignment path $(\pi_u, \pi_i)$ backward from $(N, M)$ to $(0, 0)$ without searching outside the band.
- **Band Radius Selection (`dtw_banded`)**:
  Ensures $O(N \cdot r)$ linear complexity and prevents physiologically unrealistic warping (e.g. stretching a 1-second hold to match an 8-second hold).
- **Per-Cycle vs Session Alignment**:
  - `analyse_cycle`: Evaluates individual breath cycle shape score ($100 \cdot e^{-d / 0.35}$) and tempo ratio ($100 \cdot e^{-|\ln(\text{tempo})| / 0.20}$).
  - `analyse_session`: Warps the entire continuous recording against a tiled template within a $\pm 5.0$ s window, extracting whole-session DTW distance and consistency metrics.
- **Comparison Endpoint (`POST /compare`)**:
  Downsamples the optimal warping path ($\le 300$ points) and aligned waveforms ($\le 600$ points) so the frontend can draw the aligned curves without rendering lag.

---

## 5. Biomedical Capacitive Breath Sensor Pipeline

The capacitive breath sensor models a stretchable chest/belly belt using a variable-area parallel-plate capacitor in an AC voltage divider.

### Hardware Constants

| Parameter | Symbol | Value | Description |
|---|---|---|---|
| Permittivity of Free Space | $\varepsilon_0$ | $8.854 \times 10^{-12}\text{ F/m}$ | Physical constant |
| Relative Permittivity | $\varepsilon_r$ | $3.0$ | Dielectric material (silicone/elastomer) |
| Plate Width | $W$ | $20\text{ mm}$ ($0.02\text{ m}$) | Sensor width |
| Plate Length | $L$ | $60\text{ mm}$ ($0.06\text{ m}$) | Maximum plate length |
| Initial Overlap | $x_0$ | $40\text{ mm}$ ($0.04\text{ m}$) | Resting chest overlap |
| Plate Separation | $d$ | $1\text{ mm}$ ($0.001\text{ m}$) | Dielectric gap |
| Transmission Factor | $\eta$ | $1.0$ | Mechanical coupling factor |
| Circuit Resistor | $R$ | $1\text{ M}\Omega$ | Fixed divider resistor |
| Carrier Voltage | $V_{in}$ | $5.0\text{ V}$ peak | 10 kHz excitation carrier |
| Carrier Frequency | $f$ | $10\text{ kHz}$ | Demodulated carrier frequency |
| Calibration Factor | $k$ | $50\text{ mL/mm}$ | Assumed chest expansion volume ratio |

### Demodulation & Signal Recovery Pipeline

1. **Carrier Envelope Extraction**:
   Resamples $V_{out}(t)$ to a $400\text{ kHz}$ uniform grid (40 points/cycle). Computes envelope across 10-cycle blocks ($1\text{ ms}$) via variance:
   $$\text{env}(t) = \sqrt{2 \cdot \text{Var}(V_b)}$$
2. **Low-Pass Filtering**:
   Applies a 2nd-order Butterworth filter with cutoff $f_{cut} = 2.0 \times \text{time\_scale}$ on the $1000\text{ Hz}$ envelope.
3. **Capacitance & Overlap Inversion**:
   $$C(t) = \frac{\sqrt{(V_{in} / \text{env})^2 - 1}}{\omega R}$$
   $$\text{overlap}(t) = \frac{C(t) \cdot d}{\varepsilon_0 \varepsilon_r W}$$
   $$\Delta x(t) = \frac{x_0 - \text{overlap}(t)}{\eta} \times 1000\text{ mm}$$
4. **Volume & Flow Derivation**:
   $$\text{Volume}(t) = \Delta x(t) \cdot 50\text{ mL/mm}$$
   $$\text{Flow}(t) = \frac{d}{dt}\text{Volume}(t) \quad \text{(via Savitzky-Golay differentiation)}$$

### Recovering the 5 Parameters

1. **Inhale : Hold : Exhale Ratio**: Median phase lengths and normalized form $1 : \frac{\text{Hold}}{\text{Inhale}} : \frac{\text{Exhale}}{\text{Inhale}}$.
2. **Breaths Per Minute (BPM)**: $60 / \text{median}(T_{cycle})$.
3. **Tidal Volume ($V_T$)**: Median peak-to-trough breath volume in mL (with assumed $k = 50\text{ mL/mm}$).
4. **Expiratory Flow**: Peak expiratory flow (PEF, mL/s) and Mean expiratory flow ($\text{MEF} = V_T / T_{exhale}$).
5. **Pattern Endurance**: Duration pattern was maintained before fatigue / timing deviation ($> 15\%$), plus `never_broke` status.

### How to Export from LTspice

1. Open `sensor_pipeline/breath_sensor.cir` in LTspice.
2. Run simulation (`Simulate > Run`).
3. Click the waveform window displaying `V(out)`.
4. Go to `File > Export data as text`.
5. Select `V(out)` and save as `ltspice_export.txt`.
6. Upload the file to `/sensor/analyze` or via the web UI.

### Regenerating the Stimulus (`make_pwl.py`)

To generate new synthetic breathing patterns and export updated PWL files:
```bash
cd sensor_pipeline
python make_pwl.py
```
This writes `cap_pwl.txt`, `pwl_meta.json`, and updates `breath_sensor.cir`.

---

## 6. Complete API Reference

### `GET /health`
Returns system status, loaded patterns list, and any pattern loading errors.
```bash
curl http://127.0.0.1:8000/health
```

### `GET /patterns`
Returns metadata and timings for all 10 pranayamas.
```bash
curl http://127.0.0.1:8000/patterns
```

### `POST /evaluate`
Evaluates a live chunk of breathing data.
```bash
curl -X POST http://127.0.0.1:8000/evaluate \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "test-session",
    "pattern": "sama_vritti",
    "guided": true,
    "t": [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 2.0],
    "values": [0.0, 0.05, 0.1, 0.18, 0.28, 0.4, 0.55, 0.7, 0.82, 0.91, 0.98, 1.0, 1.0, 1.0, 1.0, 0.95, 0.85, 0.7, 0.5, 0.3, 0.1]
  }'
```

### `POST /finish`
Completes a session and returns the final coaching summary.
```bash
curl -X POST http://127.0.0.1:8000/finish \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "test-session",
    "pattern": "sama_vritti",
    "guided": true,
    "t": [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 2.0],
    "values": [0.0, 0.05, 0.1, 0.18, 0.28, 0.4, 0.55, 0.7, 0.82, 0.91, 0.98, 1.0, 1.0, 1.0, 1.0, 0.95, 0.85, 0.7, 0.5, 0.3, 0.1]
  }'
```

### `POST /compare`
Computes full Sakoe-Chiba DTW alignment against the ideal template.
```bash
curl -X POST http://127.0.0.1:8000/compare \
  -H "Content-Type: application/json" \
  -d '{
    "pattern": "sama_vritti",
    "t": [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 2.0],
    "values": [0.0, 0.05, 0.1, 0.18, 0.28, 0.4, 0.55, 0.7, 0.82, 0.91, 0.98, 1.0, 1.0, 1.0, 1.0, 0.95, 0.85, 0.7, 0.5, 0.3, 0.1]
  }'
```

### `GET /sensor/sample`
Returns the 5 recovered respiratory parameters from the bundled reference wave CSV.
```bash
curl http://127.0.0.1:8000/sensor/sample
```

### `GET /sensor/wave`
Returns the bundled sensor wave downsampled to ~10 Hz for browser replay.
```bash
curl http://127.0.0.1:8000/sensor/wave
```

### `POST /sensor/analyze`
Uploads an LTspice `.txt` export or recovered wave `.csv` (up to 60 MB).
```bash
# Uploading recovered wave CSV
curl -X POST http://127.0.0.1:8000/sensor/analyze \
  -F "file=@../sensor_pipeline/breath_wave_from_ltspice.csv" \
  -F "time_scale=100"

# Uploading LTspice simulation export
curl -X POST http://127.0.0.1:8000/sensor/analyze \
  -F "file=@../sensor_pipeline/ltspice_export.txt" \
  -F "time_scale=100"
```

---

## 7. How to Add a New Pranayama

1. **Create the Reference Curve**:
   Add a curve recipe file in `backend/curves/<key>.py` with a `SEGS` definition:
   ```python
   SEGS = [('in', 4.0, 'sine'), ('hold', 4.0, 'flat'), ('out', 4.0, 'sine'), ('hold', 4.0, 'flat')]
   TITLE = "My New Pranayama"
   ```
   *Or export a CSV with columns `t_s,volume` to `backend/curves/<key>.csv`.*
2. **Register in `backend/patterns.json`**:
   Add your entry using the exact key name:
   ```json
   "my_new_pranayama": {
     "name": "My New Pranayama",
     "curve_file": "curves/my_new_pranayama.py",
     "cue": "Breathe gently and evenly.",
     "eyes_closed": true
   }
   ```
3. **Register in `frontend/app.js`**:
   Add the matching ID to the `items` array with description, subtitle, and safety contraindications.
4. **Verification**:
   Run:
   ```bash
   python -c "import pranayama_dtw as d; print(len(d.PATTERNS), d.LOAD_PROBLEMS)"
   ```
   Ensure `LOAD_PROBLEMS` remains empty.

---

## 8. Deployment & Production Considerations

- **CORS Configuration**: Configure allowed origins via the environment variable:
  ```bash
  export CORS_ORIGINS="https://mypranayamacoach.com,https://app.mypranayamacoach.com"
  ```
- **HTTPS Requirement**: Modern browsers require HTTPS to enable speech synthesis and potential microphone access. When deploying behind a reverse proxy (e.g. Nginx, Caddy), terminate TLS and forward requests to port 8000.
- **Large Upload Limits**: Ensure reverse proxies allow up to 60 MB request bodies for LTspice exports:
  ```nginx
  client_max_body_size 65M;
  ```

---

## 9. Safety, Clinical & Modelling Disclaimers

> [!IMPORTANT]
> **Educational & Engineering Prototype Only**
> 1. **Not Medical Advice**: This system is designed for educational exploration and biomechanical modeling. It has not been validated in clinical trials and does not diagnose, treat, or prevent any medical condition.
> 2. **Breath Retention & Rapid Breathing Warnings**: Prolonged breath retention (Kumbhaka) and rapid hyperventilation practices (*Bhastrika*, *Kapalabhati*) are contraindicated for individuals with uncontrolled hypertension, coronary disease, pregnancy, history of pneumothorax, epilepsy, or recent abdominal surgery. Users should stop immediately if feeling dizzy or lightheaded.
> 3. **Modelling Assumptions**: All DTW coach scoring thresholds and reference wave shapes are theoretical models tuned on simulated breath profiles.
> 4. **Biomedical Sensor Calibration**: Tidal volume and flow metrics assume a uniform calibration of $50\text{ mL}$ volume per millimeter of belt displacement. True physiological measurements require calibrated pneumotachograph integration.
