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

  // Permanently disable robotic browser SpeechSynthesis across the entire application
  if (typeof root !== "undefined" && root.speechSynthesis) {
    try {
      root.speechSynthesis.cancel();
      root.speechSynthesis.speak = function () {
        console.warn("[Voice] Native browser speech synthesis is permanently blocked.");
      };
    } catch (e) {}
  }

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
      this._initAudioBank();
      this.fetchFn = options.fetchImpl || (typeof fetch === "function" ? fetch.bind(root) : null);
      this.reset();
    }

    _initAudioBank() {
      const base = this.apiBase || "";
      const phrases = [
        "Cycle complete. Grade A, excellent rhythm. Maintain this calm diaphragmatic focus.",
        "Cycle complete. Grade B, steady breathing. Keep your exhalations smooth and controlled.",
        "Cycle complete. Fair attempt. Focus on pacing each phase closely with the guide.",
        "Cycle complete. Align your breath closely with the circle animation. Savor each breath.",
        "Needs practice. Critical timing deviation detected. Your breath is out of phase with the guide. Please synchronize with the visual circle.",
        "Timing irregularities detected. Your breathing drifted off rhythm. Focus on pacing each phase steadily with the circle.",
        "Session complete! Outstanding practice with solid rhythm and mastery. Great work!",
        "Session complete! Well done, you maintained good consistency throughout the practice.",
        "Session complete. Thank you for practicing. Consistency will bring deeper breath control.",
        "Session complete. Good effort today. Follow the visual circle closely in your next session.",
        "Practice stopped. Your breathing was out of phase with the guide. Please relax, reset, and follow the visual guide from the start.",
        "Session complete. Timing irregularities were detected during your practice. Focus on pacing each breath steadily with the guide in your next session.",
      ];
      this._audioBank = {};
      phrases.forEach((txt) => {
        const url = base + "/tts?text=" + encodeURIComponent(txt) + "&voice=" + encodeURIComponent(this.voice.neuralVoice || "en-IN-NeerjaNeural");
        const a = new Audio();
        a.preload = "auto";
        a.src = url;
        this._audioBank[txt] = a;
      });
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
      if (ss) ss.cancel();
      if (this._currentAudio) {
        this._currentAudio.pause();
        this._currentAudio = null;
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
      if (res.speak && !res.stop_session) {
        this.say(res.message);
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
      if (this.t.length < 5) return null;
      try {
        const payload = {
          session_id: this.sessionId,
          pattern: this.pattern,
          t: this.t,
          values: this.v.map((x) => +x.toFixed(4)),
        };
        const res = await this._post("/finish", payload);
        if (res && res.review_text) {
          this.onReview(res.review_text);
        }
        return res;
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

    say(text, audioUrl = null) {
      if (!this.voice.enabled || (!text && !audioUrl)) return;
      if (this.voice.speakFn) {
        this.voice.speakFn(text);
        return;
      }

      // Neutralize any browser speech synthesis
      const ss = root.speechSynthesis;
      if (ss) ss.cancel();

      if (this._currentAudio) {
        this._currentAudio.pause();
        this._currentAudio = null;
      }

      // Instant RAM playback from preloaded audio bank (< 5ms delay)
      if (text && this._audioBank && this._audioBank[text]) {
        const item = this._audioBank[text];
        this._currentAudio = item;
        item.currentTime = 0;
        item.play().catch(() => {});
        return;
      }

      const clean = this._cleanForSpeech(text || "");
      if (clean && this._audioBank && this._audioBank[clean]) {
        const item = this._audioBank[clean];
        this._currentAudio = item;
        item.currentTime = 0;
        item.play().catch(() => {});
        return;
      }

      const fullUrl = (typeof audioUrl === "string" && audioUrl) ? audioUrl : ((this.apiBase || "") + "/tts?text=" + encodeURIComponent(clean) + "&voice=" + encodeURIComponent(this.voice.neuralVoice || "en-IN-NeerjaNeural"));

      const audio = new Audio();
      this._currentAudio = audio;
      audio.preload = "auto";
      audio.src = fullUrl;
      audio.load();
      audio.play().catch((err) => {
        console.warn("[Studio Voice] Neural audio playback deferred or muted:", err);
      });
    }

    _cleanForSpeech(text) {
      return String(text)
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
