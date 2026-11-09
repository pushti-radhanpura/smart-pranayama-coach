/**
 * coach_client.js
 * ===============
 * Browser client for the Smart Pranayama Breathing Coach.
 *
 * Responsibilities:
 *  - Collects real-time breath signals at 10 Hz (client.push(t, value))
 *  - Periodically sends chunks to the backend coach (/evaluate)
 *  - Displays remarks and provides spoken guidance via browser SpeechSynthesis
 *  - Stops session on severe deviation (onStop)
 *  - Requests final DTW review on session completion (/finish)
 *  - Requests aligned ideal vs user comparison (/compare)
 */
(function (root) {
  "use strict";

  const BIN_S = 0.1; // 10 samples per second

  function makeId() {
    if (root.crypto && root.crypto.randomUUID) {
      return root.crypto.randomUUID();
    }
    return "s-" + Math.random().toString(36).slice(2) + Date.now().toString(36);
  }

  class CoachClient {
    constructor(options = {}) {
      this.apiBase = (options.apiBase || (root.location && root.location.protocol === "file:" ? "http://127.0.0.1:8000" : "")).replace(/\/$/, "");
      this.pattern = options.pattern || "sama_vritti";
      this.guided = options.guided !== false; // true = locked to guide clock
      this.checkEverySec = options.checkEverySec || 10;
      this.sessionId = options.sessionId || makeId();

      this.onMessage = options.onMessage || function () {};
      this.onStop = options.onStop || function () {};
      this.onReview = options.onReview || function () {};
      this.onError = options.onError || function (err) {
        console.warn("Pranayama Coach Client error:", err.message || err);
      };

      this.voice = {
        enabled: options.voice !== false,
        lang: options.lang || "en-US",
        rate: options.rate || 0.90,
        pitch: options.pitch || 1.0,
        selectedVoiceName: options.voiceName || null,
        speakFn: options.speakFn || null,
      };

      this._initVoices();
      this.fetchFn = options.fetchImpl || (typeof fetch === "function" ? fetch.bind(root) : null);
      this.reset();
    }

    _initVoices() {
      const ss = root.speechSynthesis;
      if (!ss) return;
      this._voices = ss.getVoices ? ss.getVoices() : [];
      if (typeof ss.addEventListener === "function") {
        ss.addEventListener("voiceschanged", () => {
          this._voices = ss.getVoices ? ss.getVoices() : [];
        });
      } else if ("onvoiceschanged" in ss) {
        ss.onvoiceschanged = () => {
          this._voices = ss.getVoices ? ss.getVoices() : [];
        };
      }
    }

    getVoices() {
      const ss = root.speechSynthesis;
      if (ss && ss.getVoices) {
        const v = ss.getVoices();
        if (v && v.length) this._voices = v;
      }
      return (this._voices || []).filter((v) => v.lang && (v.lang.startsWith("en") || v.lang.startsWith("hi")));
    }

    setVoice(voiceName) {
      this.voice.selectedVoiceName = voiceName;
    }

    reset() {
      this.t = [];
      this.v = [];
      this._lastBin = -1;
      this._lastCheck = 0;
      this._busy = false;
      this.stopped = false;
    }

    /**
     * Start/reset session. Must be triggered from a user gesture (e.g. Start button click)
     * to unlock SpeechSynthesis in modern browsers.
     */
    start() {
      this.reset();
      this.sessionId = makeId();
      const ss = root.speechSynthesis;
      if (this.voice.enabled && ss && typeof root.SpeechSynthesisUtterance !== "undefined") {
        // Silent utterance satisfies the "gesture required" browser policy
        const silent = new root.SpeechSynthesisUtterance("");
        silent.volume = 0;
        ss.speak(silent);
      }
    }

    /**
     * Push a sample into the session buffer.
     * @param {number} t - Elapsed seconds since guide start
     * @param {number} value - Breath signal amplitude (positive = inhale / chest expansion)
     */
    push(t, value) {
      if (this.stopped || !isFinite(t) || !isFinite(value)) return null;

      const bin = Math.floor(t / BIN_S + 1e-9);
      if (bin === this._lastBin) {
        this.v[this.v.length - 1] = value;
        return null;
      }
      this._lastBin = bin;
      this.t.push(+(bin * BIN_S).toFixed(2));
      this.v.push(value);

      if (t - this._lastCheck >= this.checkEverySec && !this._busy && this.t.length >= 20) {
        this._lastCheck = t;
        return this.evaluate();
      }
      return null;
    }

    async evaluate() {
      if (this._busy || this.stopped) return null;
      this._busy = true;
      try {
        const payload = {
          session_id: this.sessionId,
          pattern: this.pattern,
          guided: this.guided,
          t: this.t,
          values: this.v.map((x) => +x.toFixed(4)),
        };
        const res = await this._post("/evaluate", payload);
        if (!this.stopped) {
          this._handle(res);
        }
        return res;
      } catch (err) {
        this.onError(err);
        return null;
      } finally {
        this._busy = false;
      }
    }

    _handle(res) {
      if (res.status === "warming_up" || !res.message) return;
      this.onMessage(res);
      if (res.speak) {
        this.say(res.message, res.stop_session);
      }
      if (res.stop_session) {
        this.stopped = true;
        this.onStop(res);
      }
    }

    /**
     * Complete the session and retrieve final verbal feedback and score review.
     */
    async finish() {
      this.stopped = true;
      if (this.t.length < 20) return null;
      try {
        const payload = {
          session_id: this.sessionId,
          pattern: this.pattern,
          t: this.t,
          values: this.v.map((x) => +x.toFixed(4)),
        };
        const res = await this._post("/finish", payload);
        this.onReview(res.review);
        this.say(res.review, true);
        return res.review;
      } catch (err) {
        this.onError(err);
        return null;
      }
    }

    /**
     * Request detailed Sakoe-Chiba DTW comparison (ideal vs user aligned, cycle scores).
     */
    async compare() {
      if (this.t.length < 20) {
        throw new Error("Need at least 20 samples to compare session with ideal pattern.");
      }
      const payload = {
        pattern: this.pattern,
        t: this.t,
        values: this.v.map((x) => +x.toFixed(4)),
      };
      return this._post("/compare", payload);
    }

    /**
     * Request clinical per-cycle respiration metrics review and verbal overview.
     */
    async cycleReview(cycleNum) {
      if (this.t.length < 5) return null;
      const payload = {
        pattern: this.pattern,
        cycle_num: cycleNum,
        t: this.t,
        values: this.v.map((x) => +x.toFixed(4)),
      };
      return this._post("/cycle-review", payload);
    }

    say(text, urgent = false) {
      if (!this.voice.enabled || !text) return;
      if (this.voice.speakFn) {
        this.voice.speakFn(text, !!urgent);
        return;
      }
      const ss = root.speechSynthesis;
      if (!ss || typeof root.SpeechSynthesisUtterance === "undefined") return;

      if (ss.speaking && !urgent) return;
      if (urgent) ss.cancel();

      // Clean text for speech: eliminate any robotic formatting, colons, or parentheses
      const clean = String(text)
        .replace(/(\d+)\s*:\s*(\d+)/g, "$1 to $2")
        .replace(/(\d+)\s*:\s*(\d+)/g, "$1 to $2")
        .replace(/(\d+)\s*:\s*(\d+)/g, "$1 to $2")
        .replace(/\bBPM\b/gi, "breaths per minute")
        .replace(/\bPEF\b/gi, "peak flow")
        .replace(/\bVt\b/gi, "tidal volume")
        .replace(/\bDTW\b/gi, "alignment")
        .replace(/\(currently\s*[^)]+\)/gi, "")
        .replace(/\s*\([^)]*\)/g, "")
        .replace(/~/g, "approximately ")
        .replace(/(\d+)\s*s\b/g, "$1 seconds")
        .replace(/(\d+)\s*mL\b/g, "$1 milliliters")
        .replace(/\s+/g, " ")
        .trim();

      const u = new root.SpeechSynthesisUtterance(clean);
      u.rate = this.voice.rate || 0.90;
      u.pitch = this.voice.pitch || 1.0;
      u.volume = 1.0;

      let voices = ss.getVoices ? ss.getVoices() : [];
      if (!voices.length && "onvoiceschanged" in ss) {
        ss.onvoiceschanged = () => {
          this._voices = ss.getVoices ? ss.getVoices() : [];
          this.say(text, urgent);
        };
        return;
      }

      let pick = null;

      // 1. If user explicitly selected a voice by name
      if (this.voice.selectedVoiceName) {
        pick = voices.find((v) => v.name === this.voice.selectedVoiceName);
      }

      // 2. High priority: Google UK English Female / Google US English / Natural voices
      if (!pick) {
        pick =
          voices.find((v) => v.name && v.name.includes("UK English Female")) ||
          voices.find((v) => v.name && v.name.includes("US English")) ||
          voices.find((v) => v.name && (v.name.includes("Google") || v.name.includes("Natural") || v.name.includes("Neural")) && v.lang && v.lang.startsWith("en")) ||
          voices.find((v) => v.lang === "en-IN") ||
          voices.find((v) => v.lang === "en-GB") ||
          voices.find((v) => v.lang === "en-US") ||
          voices.find((v) => v.lang && v.lang.startsWith("en")) ||
          voices[0];
      }

      if (pick) {
        u.voice = pick;
        u.lang = pick.lang;
      }

      ss.speak(u);
    }

    async _post(path, body) {
      if (!this.fetchFn) throw new Error("Fetch API is not available");
      const url = this.apiBase + path;
      const resp = await this.fetchFn(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!resp.ok) {
        let detail = "";
        try {
          const errJson = await resp.json();
          detail = errJson.detail || "";
        } catch (_) {}
        throw new Error(`Server returned ${resp.status}${detail ? ": " + detail : ""}`);
      }
      return resp.json();
    }
  }

  async function loadPatterns(apiBase = "") {
    const url = (apiBase || "").replace(/\/$/, "") + "/patterns";
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`Could not load patterns (${resp.status})`);
    return resp.json();
  }

  /**
   * Demo breathing signal generator for testing and offline simulations.
   */
  function demoSignal(p, mode = "good") {
    const sm = (u) => u * u * (3 - 2 * u); // smoothstep
    let I = p.inhale,
      H1 = p.hold_in,
      E = p.exhale,
      H2 = p.hold_out,
      shift = 0;
    const C = p.cycle_s;
    const lag = 0.8;

    if (mode === "small") {
      // Small mistake: shorten hold, linger at bottom
      if (H1 >= 1) {
        H2 += H1 / 2;
        H1 /= 2;
      } else {
        H2 += E * 0.35;
        E *= 0.65;
      }
    } else if (mode === "wrong") {
      // Completely out of phase: 180 degrees shifted
      shift = C / 2;
    }

    const gauss = () => (Math.random() + Math.random() + Math.random() - 1.5) / 0.5;

    return function (t) {
      let x = (((t - lag - shift) % C) + C) % C;
      let v;
      if (x < I) {
        v = sm(x / I);
      } else if ((x -= I) < H1) {
        v = 1.0;
      } else if ((x -= H1) < E) {
        v = 1.0 - sm(x / E);
      } else {
        v = 0.0;
      }
      return 0.9 * v + 0.2 * Math.sin((2 * Math.PI * t) / 120) + 0.06 * gauss();
    };
  }

  const api = {
    CoachClient,
    loadPatterns,
    demoSignal,
  };

  root.PranayamaCoach = api;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
})(typeof window !== "undefined" ? window : globalThis);
