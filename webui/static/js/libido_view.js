/**
 * webui/static/js/libido_view.js — dsh-libido-panel 原生面板与白名单权限视图
 */
const LibidoView = {
  cachedData: null,
  chart: null,

  init() {
    const chkEnabled = document.getElementById("chk-libido-global-enabled");
    const chkPrivOnly = document.getElementById("chk-libido-private-only");
    if (chkEnabled) {
      chkEnabled.addEventListener("change", async (e) => {
        SFX.playPulse();
        const res = await ApiClient.manageLibido({ action: "settings", enabled: e.target.checked ? "1" : "0" });
        if (res?.success) { this.cachedData = res; this.renderAll(); }
      });
    }
    if (chkPrivOnly) {
      chkPrivOnly.addEventListener("change", async (e) => {
        SFX.playPulse();
        const res = await ApiClient.manageLibido({ action: "settings", private_only: e.target.checked ? "1" : "0" });
        if (res?.success) { this.cachedData = res; this.renderAll(); }
      });
    }

    const btnSet = document.getElementById("btn-libido-set-val");
    const inpSet = document.getElementById("input-libido-set-val");
    if (btnSet && inpSet) {
      btnSet.addEventListener("click", async () => {
        const val = parseInt(inpSet.value, 10);
        if (Number.isNaN(val)) return;
        SFX.playChime();
        const res = await ApiClient.manageLibido({ action: "set", value: val, note: `面板调至 ${val}` });
        if (res?.success) { inpSet.value = ""; this.cachedData = res; this.renderAll(); }
      });
    }

    const btnResolve = document.getElementById("btn-libido-resolve");
    if (btnResolve) {
      btnResolve.addEventListener("click", async () => {
        SFX.playChime();
        const res = await ApiClient.manageLibido({ action: "resolve", note: "面板解决一次" });
        if (res?.success) { this.cachedData = res; this.renderAll(); }
      });
    }

    const btnWake = document.getElementById("btn-libido-wake");
    if (btnWake) {
      btnWake.addEventListener("click", async () => {
        SFX.playPulse();
        const res = await ApiClient.manageLibido({ action: "wake", note: "面板手动睡醒" });
        if (res?.success) {
          this.cachedData = res;
          this.renderAll();
          if (res.message) alert(res.message);
        }
      });
    }

    const btnSaveSet = document.getElementById("btn-libido-save-settings");
    if (btnSaveSet) {
      btnSaveSet.addEventListener("click", async () => {
        const b = document.getElementById("input-libido-baseline")?.value || "";
        const sd = document.getElementById("input-libido-season-day")?.value || "";
        const sl = document.getElementById("input-libido-season-len")?.value || "";
        SFX.playPulse();
        const res = await ApiClient.manageLibido({
          action: "settings",
          baseline: b,
          season_start_day: sd,
          season_days: sl,
        });
        if (res?.success) { this.cachedData = res; this.renderAll(); }
      });
    }

    const btnAddUser = document.getElementById("btn-libido-add-user");
    const inpUid = document.getElementById("input-libido-add-uid");
    const inpLbl = document.getElementById("input-libido-add-label");
    if (btnAddUser && inpUid) {
      const doAdd = async () => {
        const uid = (inpUid.value || "").trim();
        const lbl = (inpLbl?.value || "").trim();
        if (!uid) return;
        SFX.playChime();
        const res = await ApiClient.manageLibido({ action: "user_enable", user_id: uid, label: lbl });
        if (res?.success) {
          inpUid.value = "";
          if (inpLbl) inpLbl.value = "";
          this.cachedData = res;
          this.renderAll();
        } else if (res?.error) {
          alert(`开放失败: ${res.error}`);
        }
      };
      btnAddUser.addEventListener("click", doAdd);
      inpUid.addEventListener("keydown", (e) => { if (e.key === "Enter") doAdd(); });
    }
  },

  async refresh() {
    const res = await ApiClient.getLibido();
    if (res?.success) {
      this.cachedData = res;
      this.renderAll();
    }
  },

  renderAll() {
    const d = this.cachedData;
    if (!d) return;
    const st = d.state || {};

    const numEl = document.getElementById("libido-val-num");
    const tierEl = document.getElementById("libido-tier-badge");
    const seasonEl = document.getElementById("libido-season-badge");
    const cntEl = document.getElementById("libido-users-count");
    const chkEn = document.getElementById("chk-libido-global-enabled");
    const chkPriv = document.getElementById("chk-libido-private-only");
    const preEl = document.getElementById("libido-moodline-preview");

    if (numEl) numEl.textContent = st.current ?? "--";
    if (tierEl) tierEl.textContent = st.tier || "--";
    if (seasonEl) {
      const sea = st.season || {};
      seasonEl.textContent = sea.active ? `发情期第 ${sea.day} 天` : `距发情期 ${sea.days_until ?? "-"} 天`;
    }
    if (cntEl) cntEl.textContent = d.enabled_user_count ?? 0;
    if (chkEn) chkEn.checked = Boolean(st.enabled);
    if (chkPriv) chkPriv.checked = Boolean(st.private_only);
    if (preEl) preEl.textContent = d.mood_line || "";

    const inpB = document.getElementById("input-libido-baseline");
    const inpSD = document.getElementById("input-libido-season-day");
    const inpSL = document.getElementById("input-libido-season-len");
    if (inpB && !inpB.matches(":focus")) inpB.value = st.baseline ?? 12;
    if (inpSD && !inpSD.matches(":focus")) inpSD.value = st.season_start_day ?? 18;
    if (inpSL && !inpSL.matches(":focus")) inpSL.value = st.season_days ?? 3;

    this.renderChart();
    this.renderPresets();
    this.renderUsers();
    this.renderEvents();
  },

  renderChart() {
    const el = document.getElementById("libido-curve-chart");
    if (!el || typeof echarts === "undefined") return;
    if (!this.chart) {
      this.chart = echarts.init(el);
    }
    const hist = this.cachedData?.history || [];
    const labels = hist.map(p => p.label);
    const vals = hist.map(p => p.value);
    this.chart.setOption({
      backgroundColor: "transparent",
      tooltip: { trigger: "axis" },
      grid: { left: 36, right: 16, top: 20, bottom: 26 },
      xAxis: {
        type: "category",
        data: labels,
        axisLabel: { color: "#9aa5b1", fontSize: 11, interval: 11 },
        axisLine: { lineStyle: { color: "rgba(255,255,255,0.12)" } },
      },
      yAxis: {
        type: "value",
        min: 0,
        max: 100,
        axisLabel: { color: "#9aa5b1", fontSize: 11 },
        splitLine: { lineStyle: { color: "rgba(255,255,255,0.08)" } },
      },
      series: [{
        name: "数值",
        type: "line",
        smooth: true,
        showSymbol: false,
        data: vals,
        lineStyle: { color: "#ff6b9d", width: 2.5 },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: "rgba(255,107,157,0.38)" },
            { offset: 1, color: "rgba(255,107,157,0.02)" },
          ]),
        },
      }],
    });
    this.chart.resize();
  },

  renderPresets() {
    const box = document.getElementById("libido-preset-buttons");
    if (!box) return;
    const presets = this.cachedData?.presets || [];
    box.innerHTML = presets.map(p => {
      const sign = p.delta > 0 ? `+${p.delta}` : `${p.delta}`;
      return `<button class="btn btn-sm btn-outline btn-libido-preset" data-delta="${p.delta}" data-note="${this._esc(p.note)}">${this._esc(p.label)} (${sign})</button>`;
    }).join("");
    box.querySelectorAll(".btn-libido-preset").forEach(btn => {
      btn.addEventListener("click", async () => {
        const delta = parseInt(btn.getAttribute("data-delta"), 10);
        const note = btn.getAttribute("data-note") || "";
        SFX.playPulse();
        const res = await ApiClient.manageLibido({ action: "event", delta, note });
        if (res?.success) { this.cachedData = res; this.renderAll(); }
      });
    });
  },

  renderUsers() {
    const box = document.getElementById("libido-users-list");
    if (!box) return;
    const users = this.cachedData?.users || [];
    if (users.length === 0) {
      box.innerHTML = `<div class="empty-placeholder">暂无用户记录。</div>`;
      return;
    }
    box.innerHTML = users.map(u => {
      const on = Boolean(u.enabled);
      return `
        <div class="wl-group-card ${on ? 'wl-active' : 'wl-inactive'}">
          <div class="wl-group-main">
            <div class="wl-group-avatar">${on ? '🔓' : '🔒'}</div>
            <div class="wl-group-info">
              <div class="wl-group-title-row">
                <span class="wl-group-name">${this._esc(u.nickname)}</span>
                <span class="wl-status-pill ${on ? 'pill-on' : 'pill-off'}">${on ? '已开放' : '未开放'}</span>
              </div>
              <div class="wl-group-meta">
                <span class="mono-id">QQ: ${this._esc(u.user_id)}</span>
                <span>·</span>
                <span>好感: ${Number(u.affection || 0).toFixed(0)}</span>
              </div>
            </div>
          </div>
          <div class="wl-group-actions">
            <label class="wl-switch" title="切换该用户的开放权限">
              <input type="checkbox" class="chk-libido-user-toggle" data-uid="${this._esc(u.user_id)}" data-nick="${this._esc(u.nickname)}" ${on ? 'checked' : ''}>
              <span class="wl-slider"></span>
            </label>
          </div>
        </div>
      `;
    }).join("");

    box.querySelectorAll(".chk-libido-user-toggle").forEach(chk => {
      chk.addEventListener("change", async (e) => {
        const uid = e.target.getAttribute("data-uid");
        const nick = e.target.getAttribute("data-nick") || "";
        SFX.playChime();
        const res = await ApiClient.manageLibido({ action: "user_toggle", user_id: uid, label: nick });
        if (res?.success) { this.cachedData = res; this.renderAll(); }
      });
    });
  },

  renderEvents() {
    const box = document.getElementById("libido-events-list");
    if (!box) return;
    const events = this.cachedData?.events || [];
    if (events.length === 0) {
      box.innerHTML = `<div class="empty-placeholder">暂无事件记录。</div>`;
      return;
    }
    box.innerHTML = events.slice(0, 20).map(ev => {
      const d = Number(ev.delta || 0);
      const tag = d > 0 ? `+${d}` : `${d}`;
      const ts = String(ev.at || "").replace("T", " ").slice(5, 16);
      return `
        <div class="wl-bot-card">
          <div class="wl-bot-info">
            <span class="wl-bot-icon">${d > 0 ? '🔺' : (d < 0 ? '🔻' : '📌')}</span>
            <div>
              <div class="wl-bot-name">${this._esc(ev.note)} (${tag})</div>
              <div class="wl-bot-meta">${this._esc(ts)}</div>
            </div>
          </div>
        </div>
      `;
    }).join("");
  },

  _esc(s) {
    return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
};
