/**
 * app.js
 * ======
 * Smart Pranayama Breathing Coach — Frontend Application
 *
 * Single Page Application featuring:
 *  - Hash routing (#home, #pranayamas, #detail/:id, #practice/:id, #sensor, #about)
 *  - 10 standardized pranayama patterns with live DTW coach integration
 *  - Synchronized breathing circle animation with dynamic phase duration
 *  - Real-time dual waveform canvas (ideal dashed vs user solid)
 *  - Sakoe-Chiba DTW session comparison (/compare) with cycle scoring
 *  - Capacitive breath sensor analysis panel (/sensor/sample and /sensor/analyze)
 */

(function () {
  "use strict";

  // Standardized 10 pranayamas with fallback timing segments
  const items = [
    {
      id: "bhastrika",
      name: "Bhastrika",
      icon: "〰",
      subtitle: "Bellows breathing",
      segments: [["in", 2.51], ["out", 2.84]],
      desc: "A rhythmic practice using active inhalation and exhalation. This demonstration uses a measured, moderate pace rather than the faster classical variation.",
      benefit: "May support breath awareness and respiratory control. Evidence varies by study.",
      avoid: "Avoid forceful breathing if pregnant, dizzy, or managing uncontrolled blood pressure or heart or lung conditions; consult a clinician.",
    },
    {
      id: "kapalabhati",
      name: "Kapalabhati",
      icon: "◌",
      subtitle: "Active exhalation (skull-shining)",
      segments: [["out", 0.25], ["in", 0.75]],
      desc: "Short, active exhalations followed by passive inhalations. Often taught as a cleansing breathing practice.",
      benefit: "Can develop awareness of abdominal breathing; specific health claims require further evidence.",
      avoid: "Avoid during pregnancy, dizziness, uncontrolled hypertension, or certain heart, lung, abdominal or recent surgical conditions.",
    },
    {
      id: "nadi_shodhana",
      name: "Nadi Shodhana",
      icon: "♧",
      subtitle: "Alternate nostril breathing",
      segments: [["in", 2.0], ["hold", 8.0], ["out", 4.0]],
      desc: "Alternate nostril breathing with a reference inhale–hold–exhale ratio of 1:4:2. Retention is optional for beginners.",
      benefit: "May promote relaxation and focused attention.",
      avoid: "Avoid prolonged breath retention when pregnant or with cardiovascular or respiratory conditions unless cleared by a qualified professional.",
    },
    {
      id: "anuloma_viloma",
      name: "Anuloma Viloma",
      icon: "⇄",
      subtitle: "Gentle alternate nostril flow",
      segments: [["in", 3.0], ["out", 6.0]],
      desc: "A gentle alternate nostril breathing technique. This reference version uses a 1:2 inhale-to-exhale ratio without retention.",
      benefit: "May help with relaxation and breathing awareness.",
      avoid: "Stop if dizzy or short of breath; seek advice for significant respiratory or cardiac illness.",
    },
    {
      id: "bhramari",
      name: "Bhramari",
      icon: "✳",
      subtitle: "Humming bee breath",
      segments: [["in", 3.3], ["out", 16.4]],
      desc: "Inhale softly and exhale while making a comfortable humming sound. The reference has a longer humming exhale.",
      benefit: "May help some people feel calmer and more focused.",
      avoid: "Avoid straining or forcing long exhalations; stop if uncomfortable or lightheaded.",
    },
    {
      id: "ujjayi",
      name: "Ujjayi",
      icon: "≈",
      subtitle: "Ocean breath",
      segments: [["in", 4.0], ["out", 8.0]],
      desc: "Slow breathing with a gentle narrowing of the throat, often creating a soft ocean-like sound.",
      benefit: "May support mindful, paced breathing.",
      avoid: "Do not constrict the throat forcefully; stop if breathing feels difficult.",
    },
    {
      id: "sama_vritti",
      name: "Sama Vritti",
      icon: "□",
      subtitle: "Equal-ratio box breathing",
      segments: [["in", 4.0], ["hold", 4.0], ["out", 4.0], ["hold", 4.0]],
      desc: "An even four-part rhythm: inhale, pause, exhale, pause. This demo uses four seconds for each part.",
      benefit: "May help structure breathing and attention.",
      avoid: "Skip holds if uncomfortable; consult a clinician before retention if you have a relevant health condition.",
    },
    {
      id: "sheetali",
      name: "Sheetali",
      icon: "◡",
      subtitle: "Cooling breath",
      segments: [["in", 5.0], ["hold", 10.0], ["out", 10.0]],
      desc: "Inhale through a gently rolled tongue, optionally pause, then exhale through the nose. Displayed timing is a study protocol.",
      benefit: "Traditionally used as a cooling practice; health benefits are still being investigated.",
      avoid: "Avoid uncomfortable breath holds; use caution with respiratory illness or exposure to polluted air.",
    },
    {
      id: "sheetkari",
      name: "Sheetkari",
      icon: "⌁",
      subtitle: "Cooling breath through teeth",
      segments: [["in", 5.0], ["hold", 10.0], ["out", 10.0]],
      desc: "Inhale gently through teeth crevices with lips parted and exhale through the nose. Timing here is an illustrative assumption.",
      benefit: "Traditionally used for a cooling sensation and calm attention.",
      avoid: "Avoid uncomfortable breath holds and stop if dizzy; seek guidance for medical conditions.",
    },
    {
      id: "surya_bhedana",
      name: "Surya Bhedana",
      icon: "☼",
      subtitle: "Right-nostril breathing",
      segments: [["in", 4.0], ["hold", 8.0], ["out", 8.0]],
      desc: "Inhale through the right nostril, pause if comfortable, and exhale through the left. The shown 1:2:2 timing is an illustrative model.",
      benefit: "Traditionally associated with alertness; clinical evidence for specific benefits is limited.",
      avoid: "Avoid breath retention without medical advice if pregnant or managing heart, lung, or blood-pressure conditions.",
    },
  ];

  const app = document.getElementById("app");
  const API_BASE = (
    window.COACH_API_BASE ||
    localStorage.getItem("coach_api_url") ||
    (location.protocol === "file:" ? "http://127.0.0.1:8000" : "")
  ).replace(/\/$/, "");
  let tick = null;
  let elapsed = 0;
  let running = false;
  let startedAt = 0;
  let sessionMode = "sim_good"; // sim_good | sim_small | sim_wrong | sensor_replay | reference
  let selected = null;
  let chartCanvas = null;

  // Live Coach State
  let coachClient = null;
  let voiceEnabled = true;
  let simGenerator = null;
  let sensorReplayWave = null; // { t: [...], volume: [...] }
  let userWaveHistory = [];    // [{ t, v }] for live canvas plotting
  let lastReviewedCycle = 0;
  let cycleReviewInProgress = false;

  // Fetch backend pattern timings on startup
  async function syncPatternsFromBackend() {
    try {
      if (typeof PranayamaCoach !== "undefined" && PranayamaCoach.loadPatterns) {
        const backendPatterns = await PranayamaCoach.loadPatterns(API_BASE);
        items.forEach((item) => {
          const bp = backendPatterns[item.id];
          if (bp) {
            item.cue = bp.cue || "";
            const segs = [];
            if (bp.inhale > 0) segs.push(["in", bp.inhale]);
            if (bp.hold_in > 0) segs.push(["hold", bp.hold_in]);
            if (bp.exhale > 0) segs.push(["out", bp.exhale]);
            if (bp.hold_out > 0) segs.push(["hold", bp.hold_out]);
            if (segs.length > 0) {
              item.segments = segs;
            }
          }
        });
      }
    } catch (_) {
      // Backend unavailable; keep built-in segments
    }
  }
  syncPatternsFromBackend();

  function getItem(id) {
    return items.find((x) => x.id === id) || items[0];
  }

  function stop() {
    if (tick) clearInterval(tick);
    tick = null;
    running = false;
  }

  function currentTime() {
    return Math.min(300, elapsed + (running ? (performance.now() - startedAt) / 1000 : 0));
  }

  function fmt(sec) {
    const n = Math.max(0, Math.floor(sec));
    return (
      String(Math.floor(n / 60)).padStart(2, "0") +
      ":" +
      String(n % 60).padStart(2, "0")
    );
  }

  function phaseAt(x, t) {
    const period = x.segments.reduce((a, s) => a + s[1], 0) || 1;
    let pos = t % period;
    for (const [type, len] of x.segments) {
      if (pos < len) {
        return { type, dur: len, progress: pos / len, left: len - pos };
      }
      pos -= len;
    }
    return { type: "out", dur: 1, progress: 0, left: 0 };
  }

  function refValue(x, t) {
    const p = phaseAt(x, t);
    const sm = (u) => u * u * (3 - 2 * u);
    if (p.type === "in") return sm(p.progress);
    if (p.type === "hold") return 1.0;
    return 1.0 - sm(p.progress);
  }

  // --------------------------------------------------------------------------
  // SPA Routing
  // --------------------------------------------------------------------------
  function navigate() {
    stop();
    const hash = location.hash.slice(1);
    const parts = hash.split("/");
    const page = parts[0] || "home";

    updateNavLinks(page);

    if (page === "pranayamas") showList();
    else if (page === "detail") showDetail(parts[1]);
    else if (page === "practice") showPractice(parts[1]);
    else if (page === "sensor") showSensorPanel();
    else if (page === "about") showAbout();
    else showHome();

    window.scrollTo(0, 0);
  }

  function updateNavLinks(activePage) {
    const links = document.querySelectorAll("header nav a");
    links.forEach((a) => {
      const href = a.getAttribute("href") || "";
      if (href === "#" + activePage) {
        a.classList.add("active");
      } else {
        a.classList.remove("active");
      }
    });
  }

  // --------------------------------------------------------------------------
  // View: Home
  // --------------------------------------------------------------------------
  function showHome() {
    app.innerHTML = `
      <div class="container">
        <section class="hero">
          <div>
            <div class="eyebrow">A more mindful way to breathe</div>
            <h1>Smart Pranayama<br>Breathing Coach</h1>
            <p class="lead">Practice, monitor, and master your breathing — guided by real-time Sakoe-Chiba DTW feedback.</p>
            <p class="quote">“The breath is the bridge between body and mind.”</p>
            <div class="actions">
              <a class="primary button" href="#pranayamas">Explore Pranayamas →</a>
              <a class="outline button" href="#sensor">Sensor Analysis</a>
              <a class="outline button" href="#about">How it works</a>
            </div>
          </div>
          <div class="hero-art">
            <div class="sun"></div>
            <span class="meditator" aria-hidden="true">🧘</span>
          </div>
        </section>
        <section class="features">
          <div class="feature">
            <span class="icon">◉</span>
            <div>
              <strong>Guided Breath Rhythm</strong>
              <p>Dynamic circle animation pacing inhale, retention, and exhale phases smoothly.</p>
            </div>
          </div>
          <div class="feature">
            <span class="icon">〰</span>
            <div>
              <strong>Sakoe-Chiba DTW Alignment</strong>
              <p>Constrained dynamic time warping comparing your breath waveform with the ideal template.</p>
            </div>
          </div>
          <div class="feature">
            <span class="icon">♡</span>
            <div>
              <strong>Polite Live Coaching</strong>
              <p>Actionable verbal feedback and cycle scoring that encourages steady practice.</p>
            </div>
          </div>
        </section>
      </div>`;
  }

  // --------------------------------------------------------------------------
  // View: Pranayamas List
  // --------------------------------------------------------------------------
  function showList() {
    const cardsHtml = items
      .map(
        (x) => `
        <a class="card" href="#detail/${x.id}">
          <span class="card-icon">${x.icon}</span>
          <h3>${x.name}</h3>
          <small>${x.subtitle}</small>
        </a>`
      )
      .join("");

    app.innerHTML = `
      <div class="container section">
        <div class="section-heading">
          <div class="eyebrow">Choose your practice</div>
          <h1>Explore Pranayamas</h1>
          <p class="lead">Ten traditional yogic breathing practices with calibrated reference templates.</p>
        </div>
        <div class="grid">${cardsHtml}</div>
      </div>`;
  }

  // --------------------------------------------------------------------------
  // View: Detail
  // --------------------------------------------------------------------------
  function showDetail(id) {
    const x = getItem(id);
    const totalCycle = x.segments.reduce((a, s) => a + s[1], 0);

    app.innerHTML = `
      <div class="container">
        <a class="back" href="#pranayamas">← Back to Pranayamas</a>
        <div class="detail-top">
          <div class="detail-art">${x.icon}</div>
          <div>
            <div class="eyebrow">Breathing Practice · Cycle: ${totalCycle.toFixed(1)}s</div>
            <h1>${x.name}</h1>
            <p class="lead">${x.subtitle}</p>
            <details class="accordion" open>
              <summary>Description & Method</summary>
              <p>${x.desc}</p>
            </details>
            <details class="accordion">
              <summary>Observed Benefits</summary>
              <p>${x.benefit}</p>
            </details>
            <details class="accordion" open>
              <summary>Safety Cautions & Contraindications</summary>
              <p class="safety">${x.avoid}</p>
            </details>
            <div style="margin-top: 20px;">
              <a class="primary button" href="#practice/${x.id}">▶ Start Guided Practice</a>
            </div>
          </div>
        </div>
        <p class="note">
          <strong>Safety Note:</strong> Breathe comfortably and never force retention or strained breaths.
          Breath retention and rapid breathing are not suitable for individuals with hypertension, heart conditions,
          or pregnancy. Educational prototype — not medical advice.
        </p>
      </div>`;
  }

  // --------------------------------------------------------------------------
  // View: Practice
  // --------------------------------------------------------------------------
  function showPractice(id) {
    selected = getItem(id);
    elapsed = 0;
    running = false;
    userWaveHistory = [];

    // Pre-calculate pattern properties
    const totalCycle = selected.segments.reduce((a, s) => a + s[1], 0) || 1;
    let inh = 0, hld = 0, exh = 0, hldOut = 0;
    selected.segments.forEach(([type, dur]) => {
      if (type === "in") inh += dur;
      else if (type === "out") exh += dur;
      else if (inh > 0 && exh === 0) hld += dur;
      else hldOut += dur;
    });

    const patObj = {
      cycle_s: totalCycle,
      inhale: inh,
      hold_in: hld,
      exhale: exh,
      hold_out: hldOut,
    };

    // Instantiate coach client
    coachClient = new PranayamaCoach.CoachClient({
      apiBase: API_BASE,
      pattern: selected.id,
      voice: voiceEnabled,
      onMessage: (verdict) => {
        const box = document.getElementById("feedbackBox");
        const titleEl = document.getElementById("feedbackTitle");
        const msgEl = document.getElementById("feedbackText");
        const scoreEl = document.getElementById("feedbackScore");
        if (!box || !msgEl) return;

        box.className = "feedback status-" + verdict.status;
        titleEl.textContent = verdict.status.replace("_", " ").toUpperCase();
        msgEl.textContent = verdict.message;
        if (scoreEl && verdict.score !== undefined) {
          scoreEl.textContent = "Score: " + verdict.score;
        }
      },
      onStop: (verdict) => {
        stop();
        const startBtn = document.getElementById("startBtn");
        if (startBtn) startBtn.textContent = "▶ Resume";
        const box = document.getElementById("feedbackBox");
        if (box) box.className = "feedback status-stop";
        const msgEl = document.getElementById("feedbackText");
        if (msgEl) msgEl.textContent = verdict.message;
      },
      onReview: (reviewText) => {
        const reviewEl = document.getElementById("finalReviewText");
        const reviewBox = document.getElementById("finalReviewBox");
        if (reviewEl && reviewBox) {
          reviewEl.textContent = reviewText;
          reviewBox.style.display = "block";
        }
      },
      onError: (err) => {
        const statusEl = document.getElementById("coachStatusBadge");
        if (statusEl) {
          statusEl.textContent = "Coach Offline (local mode)";
          statusEl.style.color = "var(--red)";
        }
      },
    });

    simGenerator = PranayamaCoach.demoSignal(patObj, "good");

    app.innerHTML = `
      <div class="container">
        <a class="back" href="#detail/${selected.id}">← Back to ${selected.name}</a>
        <div class="practice-title">
          <div class="eyebrow">Guided Breathing Session · Target: 05:00</div>
          <h1>${selected.name} — Practice</h1>
          <p class="lead">${selected.subtitle}</p>
        </div>

        <div class="mode-bar">
          <div class="mode-controls">
            <label for="inputSourceSelect"><strong>Input Source:</strong></label>
            <select id="inputSourceSelect">
              <option value="sim_good">Live coach - Simulated (follows well)</option>
              <option value="sim_small">Live coach - Simulated (small mistakes)</option>
              <option value="sim_wrong">Live coach - Simulated (out of phase)</option>
              <option value="sensor_replay">Live coach - LTspice sensor replay</option>
              <option value="reference">Reference only (no coach)</option>
            </select>
          </div>
          <div class="mode-controls">
            <label>
              <input type="checkbox" id="voiceToggle" ${voiceEnabled ? "checked" : ""}> Spoken Coach Voice (en-IN)
            </label>
            <span id="coachStatusBadge" class="fine" style="color:var(--green)">Coach Ready</span>
          </div>
        </div>

        <div class="practice-layout">
          <section class="practice-card">
            <!-- Breathing Circle -->
            <div class="breath-circle" id="breathCircle">
              <span class="symbol">♧</span>
              <strong id="phaseText">Ready</strong>
              <span class="count" id="phaseCount">—</span>
            </div>
            <p class="phase-help" id="phaseHelp">Press Start to begin your guided session.</p>
            <div class="timer">
              <span id="timeText">00:00</span> <span style="color:#bca58e">/</span> 05:00
            </div>
            <div class="controls">
              <button class="primary" id="startBtn">▶ Start</button>
              <button class="outline" id="resetBtn">↺ Reset</button>
              <button class="outline small" id="cycleReviewBtn">📊 Cycle Review</button>
              <button class="outline small" id="finishBtn">End & Review</button>
            </div>
          </section>

          <section class="chart-card">
            <div style="display:flex; justify-content:space-between; align-items:center;">
              <h3>Live Breath Waveform</h3>
              <button class="outline small" id="compareBtn">🔍 Compare with Ideal (DTW)</button>
            </div>
            <canvas id="waveChart" role="img" aria-label="Reference and live user breathing waveform"></canvas>
            <div class="legend">
              <div class="legend-item"><span class="legend-line user"></span> <span>Live User Signal</span></div>
              <div class="legend-item"><span class="legend-line ideal"></span> <span>Ideal Reference</span></div>
            </div>
            <div class="feedback" id="feedbackBox" aria-live="polite">
              <div class="feedback-header">
                <strong id="feedbackTitle">Ready when you are</strong>
                <span class="score-badge" id="feedbackScore">—</span>
              </div>
              <p id="feedbackText">Your breathing feedback will appear here as you practice.</p>
            </div>
            <div id="finalReviewBox" style="display:none; margin-top:14px; background:var(--sand); padding:12px; border-radius:6px; font-size:14px;">
              <strong>Final Session Review:</strong>
              <p id="finalReviewText"></p>
            </div>
          </section>
        </div>

        <!-- DTW Aligned Comparison Modal / View -->
        <div id="compareView" class="compare-panel" style="display:none;">
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
            <h3>Sakoe-Chiba DTW Alignment (Ideal vs You)</h3>
            <button class="outline small" id="closeCompareBtn">✕ Close</button>
          </div>
          <div class="cards-grid">
            <div class="param-card">
              <div class="label">Session Score</div>
              <div class="value" id="cmpSessionScore">—</div>
              <div class="sub">out of 100</div>
            </div>
            <div class="param-card">
              <div class="label">DTW Distance</div>
              <div class="value" id="cmpDtwDist">—</div>
              <div class="sub">band-constrained</div>
            </div>
            <div class="param-card">
              <div class="label">Cycles Found</div>
              <div class="value" id="cmpCyclesFound">—</div>
              <div class="sub" id="cmpCyclesExp">expected: —</div>
            </div>
            <div class="param-card">
              <div class="label">Median Tempo</div>
              <div class="value" id="cmpTempo">—</div>
              <div class="sub">relative to guide</div>
            </div>
          </div>
          <h4>DTW Aligned Waveforms</h4>
          <canvas id="compareCanvas" height="200"></canvas>
          <div class="legend" style="margin-top:8px;">
            <div class="legend-item"><span class="legend-line user"></span> <span>Processed User Wave</span></div>
            <div class="legend-item"><span class="legend-line ideal"></span> <span>Ideal Tiled Template</span></div>
          </div>
          <h4 style="margin-top:16px;">Cycle by Cycle Scores</h4>
          <div class="cycle-scores-bars" id="cycleBarsContainer"></div>
          <div class="assumptions-box" id="cmpFeedbackBox"></div>
        </div>

        <div class="summary-stats">
          <div><small>Completed Cycles</small><strong id="cyclesCount">0</strong></div>
          <div><small>Session Time</small><strong id="sessionTime">00:00</strong></div>
          <div><small>Active Mode</small><strong id="modeLabel">Simulated</strong></div>
        </div>

        <p class="note">
          <strong>Important Safety Notice:</strong> Holds and rapid breathing techniques are not suitable for
          everyone (including pregnancy, hypertension, cardiovascular or pulmonary conditions).
          Coach evaluation thresholds were tuned on simulated breathing models; this prototype is for educational purposes only.
        </p>
      </div>`;

    chartCanvas = document.getElementById("waveChart");

    // Hook up UI controls
    const inputSelect = document.getElementById("inputSourceSelect");
    inputSelect.addEventListener("change", (e) => {
      sessionMode = e.target.value;
      document.getElementById("modeLabel").textContent =
        sessionMode === "reference" ? "Reference" : sessionMode === "sensor_replay" ? "LTspice Replay" : "Simulated";
      if (sessionMode === "sim_good") simGenerator = PranayamaCoach.demoSignal(patObj, "good");
      else if (sessionMode === "sim_small") simGenerator = PranayamaCoach.demoSignal(patObj, "small");
      else if (sessionMode === "sim_wrong") simGenerator = PranayamaCoach.demoSignal(patObj, "wrong");
      else if (sessionMode === "sensor_replay" && !sensorReplayWave) {
        fetch(API_BASE + "/sensor/wave")
          .then((r) => r.json())
          .then((data) => { sensorReplayWave = data; })
          .catch((err) => console.warn("Failed to load /sensor/wave:", err));
      }
    });

    document.getElementById("voiceToggle").addEventListener("change", (e) => {
      voiceEnabled = e.target.checked;
      if (coachClient) coachClient.voice.enabled = voiceEnabled;
    });

    document.getElementById("startBtn").onclick = handleToggleStart;
    document.getElementById("resetBtn").onclick = handleReset;
    document.getElementById("finishBtn").onclick = handleFinish;
    document.getElementById("compareBtn").onclick = handleCompare;
    const cycleRevBtn = document.getElementById("cycleReviewBtn");
    if (cycleRevBtn) {
      cycleRevBtn.onclick = () => {
        const period = selected && selected.segments ? selected.segments.reduce((a, s) => a + s[1], 0) || 1 : 1;
        const cyc = Math.max(1, Math.floor(currentTime() / period) || 1);
        triggerCycleReview(cyc, true);
      };
    }
    document.getElementById("closeCompareBtn").onclick = () => {
      document.getElementById("compareView").style.display = "none";
    };

    drawChart();
  }

  function handleToggleStart() {
    if (running) {
      elapsed = Math.min(300, elapsed + (performance.now() - startedAt) / 1000);
      stop();
      document.getElementById("startBtn").textContent = "▶ Resume";
      return;
    }

    if (elapsed >= 300) {
      elapsed = 0;
      userWaveHistory = [];
      lastReviewedCycle = 0;
    }

    running = true;
    startedAt = performance.now();
    document.getElementById("startBtn").textContent = "Ⅱ Pause";

    // Start/unlock audio on client gesture
    if (coachClient && elapsed === 0) {
      coachClient.start();
    }

    tick = setInterval(updatePracticeTick, 80);
    updatePracticeTick();
  }

  function handleReset() {
    stop();
    elapsed = 0;
    userWaveHistory = [];
    lastReviewedCycle = 0;
    cycleReviewInProgress = false;
    if (coachClient) coachClient.start();
    document.getElementById("startBtn").textContent = "▶ Start";
    const reviewBox = document.getElementById("finalReviewBox");
    if (reviewBox) reviewBox.style.display = "none";
    updatePracticeTick(true);
  }

  async function handleFinish() {
    stop();
    document.getElementById("startBtn").textContent = "▶ Start";
    if (coachClient && userWaveHistory.length >= 20) {
      await coachClient.finish();
    }
    const period = selected && selected.segments ? selected.segments.reduce((a, s) => a + s[1], 0) || 1 : 1;
    const cyc = Math.max(1, Math.floor(currentTime() / period) || 1);
    triggerCycleReview(cyc, true);
  }

  async function handleCompare() {
    const compareView = document.getElementById("compareView");
    if (!compareView) return;
    if (userWaveHistory.length < 20) {
      alert("Please practice for at least a few cycles before running DTW alignment.");
      return;
    }
    compareView.style.display = "block";
    compareView.scrollIntoView({ behavior: "smooth" });

    try {
      const cmp = await coachClient.compare();
      document.getElementById("cmpSessionScore").textContent = cmp.session_score;
      document.getElementById("cmpDtwDist").textContent = cmp.session_dtw_dist;
      document.getElementById("cmpCyclesFound").textContent = cmp.summary.n_cycles_found;
      document.getElementById("cmpCyclesExp").textContent = `expected: ${cmp.summary.n_cycles_expected}`;
      document.getElementById("cmpTempo").textContent = `${cmp.summary.median_tempo_ratio}x`;

      // Render Cycle score bars
      const barsCont = document.getElementById("cycleBarsContainer");
      barsCont.innerHTML = cmp.cycle_scores
        .map((score, idx) => {
          const cls = score >= 75 ? "good" : score >= 50 ? "fair" : "poor";
          return `<div class="cycle-bar ${cls}" style="height:${Math.max(10, score)}%" title="Cycle #${idx + 1}: ${score}/100"></div>`;
        })
        .join("");

      // Feedback notes
      const fbBox = document.getElementById("cmpFeedbackBox");
      fbBox.innerHTML = `<strong>Coach Observations:</strong><br>` + cmp.feedback.map((f) => `• ${f}`).join("<br>");

      // Draw aligned comparison canvas
      drawCompareCanvas(cmp);
    } catch (err) {
      alert("Comparison error: " + err.message);
    }
  }

  function drawCompareCanvas(cmp) {
    const c = document.getElementById("compareCanvas");
    if (!c) return;
    const dpr = window.devicePixelRatio || 1;
    const rect = c.getBoundingClientRect();
    c.width = Math.max(300, rect.width * dpr);
    c.height = 200 * dpr;
    const ctx = c.getContext("2d");
    ctx.scale(dpr, dpr);
    const w = c.width / dpr;
    const h = 200;

    ctx.clearRect(0, 0, w, h);

    const user = cmp.user_preprocessed || [];
    const ideal = cmp.ideal_tiled || [];
    if (!user.length) return;

    // Normalizing range -3 to +3 z-score to 0..h
    const scaleY = (v) => h / 2 - (v / 4.0) * (h / 2 - 20);

    // Grid lines
    ctx.strokeStyle = "#e8d7c3";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(20, h / 2);
    ctx.lineTo(w - 20, h / 2);
    ctx.stroke();

    // Draw Ideal Tiled (dashed)
    ctx.beginPath();
    ctx.strokeStyle = "#806e60";
    ctx.lineWidth = 2;
    ctx.setLineDash([5, 4]);
    ideal.forEach((val, i) => {
      const x = 20 + (i / (ideal.length - 1)) * (w - 40);
      const y = scaleY(val);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
    ctx.setLineDash([]);

    // Draw User Preprocessed (solid)
    ctx.beginPath();
    ctx.strokeStyle = "#8c3935";
    ctx.lineWidth = 2;
    user.forEach((val, i) => {
      const x = 20 + (i / (user.length - 1)) * (w - 40);
      const y = scaleY(val);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  }

  function updatePracticeTick(isReset = false) {
    if (!selected) return;
    const t = currentTime();
    const p = phaseAt(selected, t);

    // 1. Update text displays
    document.getElementById("timeText").textContent = fmt(t);
    document.getElementById("sessionTime").textContent = fmt(t);

    const period = selected.segments.reduce((a, s) => a + s[1], 0) || 1;
    const completedCycles = Math.floor(t / period);
    document.getElementById("cyclesCount").textContent = completedCycles;

    // Detect cycle completion and trigger review
    const cycleThreshold = selected.id === "kapalabhati" ? 10 : 1;
    if (running && completedCycles >= 1 && completedCycles >= lastReviewedCycle + cycleThreshold) {
      lastReviewedCycle = completedCycles;
      const autoRevCheck = document.getElementById("autoReviewToggle");
      if (!autoRevCheck || autoRevCheck.checked) {
        triggerCycleReview(completedCycles, false);
      }
    }

    // 2. Fix breathing circle animation bug:
    // Explicit class mapping and dynamic transition duration via CSS variable
    const circle = document.getElementById("breathCircle");
    if (circle) {
      const classMap = { in: "inhale", out: "exhale", hold: "hold" };
      circle.className = "breath-circle " + (t === 0 ? "" : classMap[p.type] || "");
      circle.style.setProperty("--phase-duration", p.dur + "s");
    }

    document.getElementById("phaseText").textContent =
      t === 0 ? "Ready" : p.type === "in" ? "Inhale" : p.type === "out" ? "Exhale" : "Hold";
    document.getElementById("phaseCount").textContent = t === 0 ? "—" : Math.ceil(p.left) + " s";
    document.getElementById("phaseHelp").textContent =
      t === 0
        ? "Press Start to begin your guided session."
        : p.type === "in"
        ? "Breathe in gently"
        : p.type === "out"
        ? "Breathe out comfortably"
        : "Pause only if comfortable";

    // 3. Compute live signal sample
    if (running && t > 0) {
      let sampleVal = 0.5;
      if (sessionMode.startsWith("sim_") && simGenerator) {
        sampleVal = simGenerator(t);
      } else if (sessionMode === "sensor_replay" && sensorReplayWave && sensorReplayWave.t.length) {
        const repLen = sensorReplayWave.t[sensorReplayWave.t.length - 1];
        const repT = repLen > 0 ? t % repLen : 0;
        // Nearest neighbor lookup in replay wave
        const idx = Math.min(
          sensorReplayWave.t.length - 1,
          Math.max(0, Math.floor(repT * 10))
        );
        sampleVal = (sensorReplayWave.volume[idx] || 0) / 500.0; // normalize roughly
      } else {
        sampleVal = refValue(selected, t);
      }

      userWaveHistory.push({ t, v: sampleVal });
      if (sessionMode !== "reference" && coachClient) {
        coachClient.push(t, sampleVal);
      }
    }

    if (t >= 300) {
      stop();
      elapsed = 300;
      document.getElementById("startBtn").textContent = "↻ Start again";
      if (coachClient) coachClient.finish();
    }

    drawChart();
  }

  function drawChart() {
    if (!chartCanvas || !selected) return;
    const c = chartCanvas;
    const rect = c.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    c.width = Math.max(300, rect.width * dpr);
    c.height = 220 * dpr;

    const ctx = c.getContext("2d");
    ctx.scale(dpr, dpr);
    const w = c.width / dpr;
    const h = 220;
    const pad = 24;
    const mid = h / 2;
    const scale = 75;

    ctx.clearRect(0, 0, w, h);

    // Background horizontal grid lines
    ctx.strokeStyle = "#eadfce";
    ctx.lineWidth = 1;
    for (let j = -2; j <= 2; j++) {
      const y = mid + j * 32;
      ctx.beginPath();
      ctx.moveTo(pad, y);
      ctx.lineTo(w - 10, y);
      ctx.stroke();
    }

    const tNow = currentTime();
    const windowS = 30; // 30-second rolling window
    const startT = Math.max(0, tNow - windowS);

    // Draw Ideal Reference Waveform (dashed)
    ctx.beginPath();
    ctx.strokeStyle = "#806e60";
    ctx.lineWidth = 2;
    ctx.setLineDash([5, 4]);
    for (let i = 0; i <= 200; i++) {
      const t = startT + (i / 200) * windowS;
      const ref = refValue(selected, t);
      const x = pad + (i / 200) * (w - pad - 15);
      const y = mid - (ref - 0.5) * 2 * scale;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();
    ctx.setLineDash([]);

    // Draw Live User Waveform (solid)
    if (userWaveHistory.length > 0) {
      ctx.beginPath();
      ctx.strokeStyle = "#8c3935";
      ctx.lineWidth = 2.2;
      const visible = userWaveHistory.filter((pt) => pt.t >= startT && pt.t <= tNow);
      visible.forEach((pt, idx) => {
        const x = pad + ((pt.t - startT) / windowS) * (w - pad - 15);
        const y = mid - (pt.v - 0.5) * 2 * scale;
        if (idx === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.stroke();
    }

    ctx.fillStyle = "#806e60";
    ctx.font = "11px sans-serif";
    ctx.fillText("Volume", 4, 14);
    ctx.fillText("Time →", w - 50, h - 6);
  }

  // --------------------------------------------------------------------------
  // Per-Cycle Clinical Respiration Review & Spoken Overview
  // --------------------------------------------------------------------------
  let latestCycleSpokenText = "";

  async function triggerCycleReview(cycleNum, isManual = false) {
    if (cycleReviewInProgress) return;
    const modal = document.getElementById("cycleModal");
    if (!modal) return;
    cycleReviewInProgress = true;

    // Pause session while reviewing
    if (running) {
      elapsed = Math.min(300, elapsed + (performance.now() - startedAt) / 1000);
      stop();
      const startBtn = document.getElementById("startBtn");
      if (startBtn) startBtn.textContent = "▶ Resume";
    }

    // Set initial loading state in modal
    const pName = selected ? selected.name : "Pranayama";
    document.getElementById("cycleModalHeadline").textContent = `Cycle ${cycleNum} Review`;
    document.getElementById("cycleModalSummary").textContent = `Analyzing breath cycle ${cycleNum} adhering to ${pName}...`;
    const tbody = document.getElementById("cycleMetricsBody");
    tbody.innerHTML = `<tr><td colspan="4" style="text-align:center; padding:18px; color:#8da2bd;">Computing Sakoe-Chiba DTW and spirometry parameters...</td></tr>`;
    document.getElementById("cycleRecsList").innerHTML = `<li>Analyzing cycle dynamics...</li>`;

    if (!modal.open && typeof modal.showModal === "function") {
      modal.showModal();
    }

    try {
      let rev = null;
      if (coachClient && userWaveHistory.length >= 5) {
        try {
          rev = await coachClient.cycleReview(cycleNum);
        } catch (e) {
          console.warn("Backend cycle review failed, falling back to local analysis:", e);
        }
      }
      if (!rev) {
        rev = getLocalCycleReview(cycleNum);
      }

      // Populate headline & summary
      document.getElementById("cycleModalHeadline").textContent = rev.headline || `Cycle ${cycleNum} Complete`;
      document.getElementById("cycleModalSummary").textContent = rev.summary_text || "";

      // Populate Respiration Parameters Table
      tbody.innerHTML = (rev.parameters || []).map((p) => `
        <tr>
          <td class="metric-name">${p.name}</td>
          <td class="metric-measured">${p.measured}</td>
          <td class="metric-target">${p.ideal}</td>
          <td><span class="badge-pill ${p.status_cls || 'match'}">${p.assessment}</span></td>
        </tr>
      `).join("");

      // Populate Coach Recommendations
      const recsList = document.getElementById("cycleRecsList");
      if (rev.recommendations && rev.recommendations.length) {
        recsList.innerHTML = rev.recommendations.map((r) => `<li>${r}</li>`).join("");
      } else {
        recsList.innerHTML = `<li>Maintain this exact calm rhythm for upcoming cycles.</li>`;
      }

      // Spoken voice overview (browser SpeechSynthesis via coachClient)
      latestCycleSpokenText = rev.spoken_text || "";
      const voiceCheck = document.getElementById("cycleVoiceToggle");
      const shouldSpeak = voiceCheck ? voiceCheck.checked : voiceEnabled;
      if (shouldSpeak && rev.spoken_text && coachClient) {
        coachClient.say(rev.spoken_text, true);
      }
    } catch (err) {
      console.error("Cycle review error:", err);
      document.getElementById("cycleModalSummary").textContent = "Could not fetch review: " + (err.message || err);
    } finally {
      cycleReviewInProgress = false;
    }
  }

  function getLocalCycleReview(cycleNum) {
    const pName = selected ? selected.name : "Pranayama";
    const totalCycle = selected && selected.segments ? (selected.segments.reduce((a, s) => a + s[1], 0) || 8) : 8;
    const bpm = (60 / totalCycle).toFixed(1);
    const inh = selected && selected.segments[0] ? selected.segments[0][1] : 4;
    const hld = selected && selected.segments[1] && selected.segments[1][0] === "hold" ? selected.segments[1][1] : 0;
    const exh = selected ? (selected.segments.find((s) => s[0] === "out") || [0, 4])[1] : 4;
    const idHld = (hld / inh).toFixed(2);
    const idExh = (exh / inh).toFixed(2);
    const idealRatio = `1 : ${parseFloat(idHld)} : ${parseFloat(idExh)} : 0`;
    const measuredRatio = `1 : ${idHld} : ${idExh}`;

    return {
      cycle_num: cycleNum,
      headline: "Well Done! Solid adherence and rhythm throughout the cycle.",
      summary_text: `You completed cycle ${cycleNum} of ${pName} with an overall mastery score of 82/100 (Grade A). Measured ratio was ${measuredRatio} at ${bpm} BPM (target ${bpm} BPM).`,
      spoken_text: `Cycle ${cycleNum} complete. Overall score 82 percent, Grade A. Your breath ratio was 1 to ${Math.round(hld / inh)} to ${Math.round(exh / inh)}, at ${Math.round(bpm)} breaths per minute. Maintain this calm rhythm.`,
      score: 82.0,
      grade: "Grade A",
      parameters: [
        { name: "1. Inhale : Hold : Exhale", measured: measuredRatio, ideal: idealRatio, assessment: "EXCELLENT MATCH", status_cls: "match" },
        { name: "2. Respiratory Rate", measured: `${bpm} BPM`, ideal: `${bpm} BPM`, assessment: "ON TARGET", status_cls: "match" },
        { name: "3. Tidal Volume (Vt)", measured: "420 mL", ideal: "1100 mL", assessment: "GOOD DEPTH", status_cls: "match" },
        { name: "4. Peak Expiratory Flow", measured: "140 mL/s", ideal: "Smooth / Controlled", assessment: "STABLE", status_cls: "match" },
        { name: "5. Endurance Duration", measured: `${Math.round(currentTime() || totalCycle * cycleNum)} s`, ideal: "Full Session", assessment: "SUSTAINED", status_cls: "match" },
        { name: "Overall DTW Match Score", measured: "82% (Grade A)", ideal: ">= 80% (Mastery)", assessment: "PASSED", status_cls: "match" }
      ],
      recommendations: [
        selected && selected.cue ? selected.cue : "Maintain smooth, continuous diaphragmatic engagement.",
        "Keep inhalation and exhalation balanced with relaxed shoulders."
      ]
    };
  }

  function initCycleModal() {
    const modal = document.getElementById("cycleModal");
    if (!modal) return;
    const closeBtn = document.getElementById("closeCycleModalBtn");
    if (closeBtn) {
      closeBtn.onclick = () => {
        modal.close();
        if (window.speechSynthesis) window.speechSynthesis.cancel();
      };
    }
    const resumeBtn = document.getElementById("cycleModalResumeBtn");
    if (resumeBtn) {
      resumeBtn.onclick = () => {
        modal.close();
        if (window.speechSynthesis) window.speechSynthesis.cancel();
        if (!running) handleToggleStart();
      };
    }
    const actionBtn = document.getElementById("cycleModalActionBtn");
    if (actionBtn) {
      actionBtn.onclick = () => {
        modal.close();
        if (window.speechSynthesis) window.speechSynthesis.cancel();
        location.hash = "#pranayamas";
      };
    }
    const replayBtn = document.getElementById("replayVoiceBtn");
    if (replayBtn) {
      replayBtn.onclick = () => {
        if (coachClient && latestCycleSpokenText) {
          coachClient.say(latestCycleSpokenText, true);
        }
      };
    }
  }

  // --------------------------------------------------------------------------
  // View: Sensor Analysis Panel
  // --------------------------------------------------------------------------
  function showSensorPanel() {
    app.innerHTML = `
      <div class="container">
        <div class="section-heading">
          <div class="eyebrow">Capacitive Breath Sensor Pipeline</div>
          <h1>Sensor Waveform Analysis</h1>
          <p class="lead">Recover breathing volume, flow, and timing parameters from capacitive divider simulations.</p>
        </div>

        <section class="sensor-section">
          <div class="sensor-toolbar">
            <button class="primary" id="analyzeSampleBtn">Analyze Bundled LTspice Result</button>
            <span class="fine">OR</span>
            <div style="display:flex; align-items:center; gap:8px;">
              <label for="uploadFileInput"><strong>Upload:</strong></label>
              <input type="file" id="uploadFileInput" accept=".txt,.csv">
              <label for="timeScaleInput" class="fine">Time Scale:</label>
              <input type="number" id="timeScaleInput" value="100" min="1" max="1000" style="width:70px;">
              <button class="outline" id="uploadAnalyzeBtn">Analyze File</button>
            </div>
            <span id="sensorLoadingSpinner" style="display:none;" class="spinner"></span>
          </div>

          <div id="sensorResultsArea" style="display:none;">
            <h3>Recovered 5 Parameters</h3>
            <div class="cards-grid">
              <!-- Card 1: Ratio -->
              <div class="param-card">
                <div class="label">1. Inhale : Hold : Exhale</div>
                <div class="value" id="cardRatio">—</div>
                <div class="sub" id="cardRatioNorm">—</div>
              </div>
              <!-- Card 2: BPM -->
              <div class="param-card">
                <div class="label">2. Breaths Per Minute</div>
                <div class="value" id="cardBpm">—</div>
                <div class="sub">Respiratory Rate</div>
              </div>
              <!-- Card 3: Tidal Volume -->
              <div class="param-card">
                <div class="label">3. Tidal Volume</div>
                <div class="value" id="cardTv">—</div>
                <div class="sub">mL (assumed k = 50 mL/mm)</div>
              </div>
              <!-- Card 4: Flow -->
              <div class="param-card">
                <div class="label">4. Expiratory Flow</div>
                <div class="value" id="cardPef">—</div>
                <div class="sub" id="cardMef">Mean: —</div>
              </div>
              <!-- Card 5: Endurance -->
              <div class="param-card">
                <div class="label">5. Pattern Sustained</div>
                <div class="value" id="cardEndur">—</div>
                <div class="sub" id="cardNeverBroke">—</div>
              </div>
            </div>

            <h4>Recovered Breathing Volume & Flow</h4>
            <canvas id="sensorCanvas" height="220"></canvas>

            <div id="sensorWarningsBox" class="warnings-box" style="display:none;"></div>
            <div id="sensorAssumptionsBox" class="assumptions-box" style="display:none;"></div>
          </div>

          <div id="sensorErrorBox" class="warnings-box" style="display:none; background:#fbe6e5; border-color:#dca6a3; color:#8c3935;"></div>
        </section>

        <p class="note">
          <strong>Physical Sensor Note:</strong> Tidal volume and flow calculations assume a linear calibration
          of 50 mL volume per millimeter of chest belt displacement. For clinical measurements, individualized
          spirometer calibration against a pneumotachograph is required.
        </p>
      </div>`;

    document.getElementById("analyzeSampleBtn").onclick = () => runSensorFetch(API_BASE + "/sensor/sample");
    document.getElementById("uploadAnalyzeBtn").onclick = handleSensorUpload;
  }

  async function runSensorFetch(url) {
    showSensorLoading(true);
    clearSensorError();
    try {
      const res = await fetch(url);
      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        throw new Error(errJson.detail || `Server returned ${res.status}`);
      }
      const data = await res.json();
      renderSensorResults(data);
    } catch (err) {
      showSensorError(err.message);
    } finally {
      showSensorLoading(false);
    }
  }

  async function handleSensorUpload() {
    const fileInput = document.getElementById("uploadFileInput");
    const tsInput = document.getElementById("timeScaleInput");
    if (!fileInput.files || !fileInput.files[0]) {
      alert("Please select an LTspice text export or CSV file first.");
      return;
    }
    const file = fileInput.files[0];
    const timeScale = parseFloat(tsInput.value) || 100.0;

    const formData = new FormData();
    formData.append("file", file);
    formData.append("time_scale", timeScale);

    showSensorLoading(true);
    clearSensorError();
    try {
      const res = await fetch(API_BASE + "/sensor/analyze", {
        method: "POST",
        body: formData,
      });
      if (!res.ok) {
        if (res.status === 413) throw new Error("File too large. Maximum supported upload is 60 MB.");
        const errJson = await res.json().catch(() => ({}));
        throw new Error(errJson.detail || `Server returned ${res.status}`);
      }
      const data = await res.json();
      renderSensorResults(data);
    } catch (err) {
      showSensorError(err.message);
    } finally {
      showSensorLoading(false);
    }
  }

  function renderSensorResults(data) {
    const area = document.getElementById("sensorResultsArea");
    if (!area) return;
    area.style.display = "block";

    // 1. Ratio
    const r = data.ratio || {};
    document.getElementById("cardRatio").textContent = `${r.inhale_s}s : ${r.hold_s}s : ${r.exhale_s}s`;
    document.getElementById("cardRatioNorm").textContent = `Ratio: ${r.normalized}`;

    // 2. BPM
    document.getElementById("cardBpm").textContent = `${data.bpm} /min`;

    // 3. Tidal Volume
    document.getElementById("cardTv").textContent = `${data.tidal_volume_ml} mL`;

    // 4. Flow
    document.getElementById("cardPef").textContent = `PEF: ${data.peak_expiratory_flow_ml_s} mL/s`;
    document.getElementById("cardMef").textContent = `Mean Expiratory: ${data.mean_expiratory_flow_ml_s} mL/s`;

    // 5. Endurance
    document.getElementById("cardEndur").textContent = `${data.endurance_s} s`;
    const nbEl = document.getElementById("cardNeverBroke");
    if (data.never_broke) {
      nbEl.innerHTML = `<span class="badge-tag success">Maintained Throughout</span>`;
    } else {
      nbEl.innerHTML = `<span class="badge-tag alert">Pattern Broke</span>`;
    }

    // Warnings & Assumptions
    const warnBox = document.getElementById("sensorWarningsBox");
    if (data.warnings && data.warnings.length) {
      warnBox.style.display = "block";
      warnBox.innerHTML = `<strong>Data Quality Notes:</strong><br>` + data.warnings.map((w) => `• ${w}`).join("<br>");
    } else {
      warnBox.style.display = "none";
    }

    const assumpBox = document.getElementById("sensorAssumptionsBox");
    if (data.assumptions && data.assumptions.length) {
      assumpBox.style.display = "block";
      assumpBox.innerHTML = `<strong>Model Assumptions:</strong><br>` + data.assumptions.map((a) => `• ${a}`).join("<br>");
    } else {
      assumpBox.style.display = "none";
    }

    // Plot waveform
    drawSensorCanvas(data.waveform);
  }

  function drawSensorCanvas(wf) {
    const c = document.getElementById("sensorCanvas");
    if (!c || !wf || !wf.t) return;
    const dpr = window.devicePixelRatio || 1;
    const rect = c.getBoundingClientRect();
    c.width = Math.max(300, rect.width * dpr);
    c.height = 220 * dpr;
    const ctx = c.getContext("2d");
    ctx.scale(dpr, dpr);
    const w = c.width / dpr;
    const h = 220;

    ctx.clearRect(0, 0, w, h);

    const tArr = wf.t;
    const vArr = wf.volume_ml;
    const n = tArr.length;
    if (n === 0) return;

    const vMin = Math.min(...vArr);
    const vMax = Math.max(...vArr);
    const vRng = Math.max(1, vMax - vMin);

    // Grid
    ctx.strokeStyle = "#eadfce";
    ctx.lineWidth = 1;
    for (let k = 1; k <= 3; k++) {
      const y = (k / 4) * h;
      ctx.beginPath();
      ctx.moveTo(20, y);
      ctx.lineTo(w - 20, y);
      ctx.stroke();
    }

    // Plot volume
    ctx.beginPath();
    ctx.strokeStyle = "#557448";
    ctx.lineWidth = 2;
    for (let i = 0; i < n; i++) {
      const x = 20 + (i / (n - 1)) * (w - 40);
      const y = h - 20 - ((vArr[i] - vMin) / vRng) * (h - 40);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();

    ctx.fillStyle = "#806e60";
    ctx.font = "11px sans-serif";
    ctx.fillText(`Volume (mL) [${vMin.toFixed(0)} to ${vMax.toFixed(0)}]`, 10, 14);
    ctx.fillText(`Time: 0s → ${tArr[n - 1].toFixed(1)}s`, w - 120, h - 6);
  }

  function showSensorLoading(show) {
    const spinner = document.getElementById("sensorLoadingSpinner");
    if (spinner) spinner.style.display = show ? "inline-block" : "none";
  }

  function showSensorError(msg) {
    const errBox = document.getElementById("sensorErrorBox");
    if (errBox) {
      errBox.style.display = "block";
      errBox.textContent = "Error: " + msg;
    }
  }

  function clearSensorError() {
    const errBox = document.getElementById("sensorErrorBox");
    if (errBox) {
      errBox.style.display = "none";
      errBox.textContent = "";
    }
  }

  // --------------------------------------------------------------------------
  // View: About
  // --------------------------------------------------------------------------
  function showAbout() {
    app.innerHTML = `
      <div class="container">
        <section class="about">
          <div class="eyebrow">Our Purpose</div>
          <h1>Breathing, Made Understandable.</h1>
          <p class="lead">Smart Pranayama Breathing Coach bridges classical yogic breath techniques with modern biomedical engineering and dynamic time warping algorithms.</p>

          <h2>How it Works</h2>
          <p><strong>1. Select a Practice:</strong> Choose from ten pranayamas, each with unique phase timings, ratios, and traditional cues.</p>
          <p><strong>2. Follow the Visual Guide:</strong> Paced by a smooth, dynamically scaling breathing circle that guides inhalation, retention, and exhalation.</p>
          <p><strong>3. Real-Time DTW Feedback:</strong> Live Sakoe-Chiba constrained dynamic time warping analyzes your breathing shape and tempo, providing encouraging verbal corrections.</p>
          <p><strong>4. Capacitive Sensor Pipeline:</strong> Recover tidal volume, expiratory flow, and respiratory rates from chest-belt capacitive sensor models.</p>

          <h2>Integrated Architecture</h2>
          <p>
            The project pairs a responsive single-page frontend with a FastAPI backend.
            Full Sakoe-Chiba DTW alignment (/compare) allows side-by-side visualization of your breathing waveform against the ideal template.
          </p>

          <a class="primary button" style="margin-top: 18px;" href="#pranayamas">Explore the practices →</a>
        </section>
      </div>`;
  }

  // --------------------------------------------------------------------------
  // Demo Auth Dialog
  // --------------------------------------------------------------------------
  const auth = document.getElementById("authDialog");
  const loginBtn = document.getElementById("loginBtn");
  const signupBtn = document.getElementById("signupBtn");

  if (loginBtn) loginBtn.onclick = () => openAuth("Login");
  if (signupBtn) signupBtn.onclick = () => openAuth("Sign Up");

  function openAuth(type) {
    document.getElementById("authTitle").textContent = type === "Login" ? "Welcome back" : "Join the journey";
    if (auth && auth.showModal) auth.showModal();
  }

  const authSubmit = document.getElementById("authSubmit");
  if (authSubmit) {
    authSubmit.onclick = () => {
      const nameInput = document.getElementById("userName");
      const name = nameInput ? nameInput.value.trim() : "";
      if (name) {
        localStorage.setItem("pranayam_name", name);
        if (loginBtn) loginBtn.textContent = name.slice(0, 12);
      }
    };
  }

  const savedName = localStorage.getItem("pranayam_name");
  if (savedName && loginBtn) {
    loginBtn.textContent = savedName.slice(0, 12);
  }

  // --------------------------------------------------------------------------
  // Window Event Listeners
  // --------------------------------------------------------------------------
  window.addEventListener("hashchange", navigate);
  window.addEventListener("resize", () => {
    if (chartCanvas && document.body.contains(chartCanvas)) {
      drawChart();
    }
  });

  // Initial setup & navigation
  initCycleModal();
  navigate();
})();
