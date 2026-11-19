/**
 * webui/static/js/sfx.js — 赛博空间纯合成 Web Audio 音效引擎
 * 无需加载任何音频文件，100% 由浏览器音频振荡器实时合成
 */
const SFX = {
  ctx: null,
  enabled: true,

  init() {
    const saved = localStorage.getItem("hoshimi_webui_sfx");
    this.enabled = saved !== "false";

    const soundBtn = document.getElementById("btn-toggle-sound");
    if (soundBtn) {
      this.updateButtonUI(soundBtn);
      soundBtn.addEventListener("click", () => {
        this.enabled = !this.enabled;
        localStorage.setItem("hoshimi_webui_sfx", this.enabled);
        this.updateButtonUI(soundBtn);
        if (this.enabled) this.playChime();
      });
    }
  },

  updateButtonUI(btn) {
    if (this.enabled) {
      btn.innerHTML = `<span class="icon">🔊</span> <span>音效: 开</span>`;
      btn.style.color = "#ec4899";
    } else {
      btn.innerHTML = `<span class="icon">🔇</span> <span>音效: 关</span>`;
      btn.style.color = "#6b7280";
    }
  },

  _ensureCtx() {
    if (!this.ctx) {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (AudioCtx) this.ctx = new AudioCtx();
    }
    if (this.ctx && this.ctx.state === "suspended") {
      this.ctx.resume();
    }
  },

  playChime() {
    if (!this.enabled) return;
    try {
      this._ensureCtx();
      if (!this.ctx) return;
      const now = this.ctx.currentTime;

      const osc = this.ctx.createOscillator();
      const gain = this.ctx.createGain();

      osc.type = "sine";
      osc.frequency.setValueAtTime(587.33, now); // D5
      osc.frequency.exponentialRampToValueAtTime(880.0, now + 0.12); // A5

      gain.gain.setValueAtTime(0.04, now);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.35);

      osc.connect(gain);
      gain.connect(this.ctx.destination);

      osc.start(now);
      osc.stop(now + 0.36);
    } catch (_) {}
  },

  playPulse() {
    if (!this.enabled) return;
    try {
      this._ensureCtx();
      if (!this.ctx) return;
      const now = this.ctx.currentTime;

      const osc = this.ctx.createOscillator();
      const gain = this.ctx.createGain();

      osc.type = "triangle";
      osc.frequency.setValueAtTime(440.0, now);
      osc.frequency.exponentialRampToValueAtTime(659.25, now + 0.08);

      gain.gain.setValueAtTime(0.03, now);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.2);

      osc.connect(gain);
      gain.connect(this.ctx.destination);

      osc.start(now);
      osc.stop(now + 0.21);
    } catch (_) {}
  }
};

