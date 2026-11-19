/**
 * webui/static/js/qzone_care_view.js — QQ 空间特别关注（特关）与三档巡检面板
 */
const QzoneCareView = {
  cachedData: null,

  init() {
    const btnAdd = document.getElementById("btn-add-qzone-care");
    const inputUid = document.getElementById("input-qzone-care-uid");
    const inputNick = document.getElementById("input-qzone-care-nick");
    const inputReason = document.getElementById("input-qzone-care-reason");

    if (btnAdd && inputUid) {
      const doAdd = async () => {
        const uid = inputUid.value.trim();
        const nick = inputNick ? inputNick.value.trim() : "";
        const reason = inputReason ? inputReason.value.trim() : "";
        if (!uid) return;
        SFX.playChime();
        const res = await ApiClient.manageQzoneCare("add", uid, nick, reason);
        if (res?.success) {
          inputUid.value = "";
          if (inputNick) inputNick.value = "";
          if (inputReason) inputReason.value = "";
          this.cachedData = res;
          this.renderAll();
        } else {
          alert(`加入特关失败: ${res?.error || "未知错误"}`);
        }
      };

      btnAdd.addEventListener("click", doAdd);
      inputUid.addEventListener("keydown", (e) => {
        if (e.key === "Enter") doAdd();
      });
    }

    const searchCand = document.getElementById("qzone-candidate-search");
    if (searchCand) {
      searchCand.addEventListener("input", () => this.renderCandidates());
    }
  },

  async refresh() {
    const res = await ApiClient.getQzoneCare();
    if (res?.success) {
      this.cachedData = res;
      this.renderAll();
    }
  },

  renderAll() {
    if (!this.cachedData) return;
    const sum = this.cachedData.summary || {};

    const elTotal = document.getElementById("qc-count-total");
    const elBff = document.getElementById("qc-count-bff");
    const elManual = document.getElementById("qc-count-manual");
    const elSlots = document.getElementById("qc-today-slots");

    if (elTotal) elTotal.textContent = sum.total_care ?? 0;
    if (elBff) elBff.textContent = sum.bff_auto_count ?? 0;
    if (elManual) elManual.textContent = sum.manual_count ?? 0;
    if (elSlots) {
      const done = sum.today_slots_done || [];
      elSlots.textContent = done.length > 0 ? `今日已巡: ${done.join(" / ")}` : "今日待巡检 (08:30 / 14:30 / 20:30)";
    }

    this.renderSpecialList();
    this.renderCandidates();
    this.renderPatrolLogs();
  },

  _sourceBadge(src) {
    if (src === "master") return `<span class="tier-pill tier-pill-bff">👑 主人永久特关</span>`;
    if (src === "bff_auto") return `<span class="tier-pill tier-pill-friend">💖 挚友自动特关</span>`;
    if (src === "bot") return `<span class="tier-pill tier-pill-acq">🐾 弥酱自加</span>`;
    return `<span class="tier-pill tier-pill-acq">📌 手动特关</span>`;
  },

  renderSpecialList() {
    const container = document.getElementById("qzone-care-list");
    if (!container) return;

    const list = this.cachedData?.special_care || [];
    if (list.length === 0) {
      container.innerHTML = `<div class="empty-placeholder">暂无特别关注用户。</div>`;
      return;
    }

    container.innerHTML = list.map(item => `
      <div class="wl-bot-card" style="gap: 12px;">
        <div class="wl-bot-info" style="gap: 10px; min-width: 0; flex: 1;">
          <img src="${this._escape(item.avatar_url)}" alt="" style="width:36px;height:36px;border-radius:50%;object-fit:cover;flex-shrink:0;">
          <div style="min-width: 0;">
            <div class="wl-bot-name" style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
              <span>${this._escape(item.nickname)}</span>
              <span style="font-size:12px;opacity:0.75;">(${this._escape(item.uin)} · 好感 ${item.affection})</span>
              ${this._sourceBadge(item.source)}
            </div>
            <div class="wl-bot-meta">${this._escape(item.reason || "重点关注空间动态")}</div>
          </div>
        </div>
        ${item.removable ? `
          <button class="btn btn-sm btn-outline btn-remove-qzone-care" data-uid="${this._escape(item.uin)}">移出</button>
        ` : `<span class="card-tag">受保护</span>`}
      </div>
    `).join("");

    container.querySelectorAll(".btn-remove-qzone-care").forEach(btn => {
      btn.addEventListener("click", async () => {
        const uid = btn.getAttribute("data-uid");
        SFX.playPulse();
        const res = await ApiClient.manageQzoneCare("remove", uid);
        if (res?.success) {
          this.cachedData = res;
          this.renderAll();
        } else {
          alert(`移出失败: ${res?.error || "未知错误"}`);
        }
      });
    });
  },

  renderCandidates() {
    const container = document.getElementById("qzone-candidate-list");
    if (!container) return;

    const kw = (document.getElementById("qzone-candidate-search")?.value || "").trim().toLowerCase();
    const list = (this.cachedData?.candidates || []).filter(c => {
      if (!kw) return true;
      return String(c.nickname).toLowerCase().includes(kw) || String(c.uin).includes(kw);
    });

    if (list.length === 0) {
      container.innerHTML = `<div class="empty-placeholder">没有匹配的候选好友，可在上方直接输入 QQ 号添加。</div>`;
      return;
    }

    container.innerHTML = list.slice(0, 20).map(c => `
      <div class="wl-bot-card">
        <div class="wl-bot-info">
          <span class="wl-bot-icon">👤</span>
          <div>
            <div class="wl-bot-name">${this._escape(c.nickname)} (${this._escape(c.uin)})</div>
            <div class="wl-bot-meta">好感度: ${c.affection}${c.is_excluded_bff ? " · 曾手动取消挚友默认特关" : ""}</div>
          </div>
        </div>
        <button class="btn btn-sm btn-primary btn-quick-add-care" data-uid="${this._escape(c.uin)}" data-nick="${this._escape(c.nickname)}">
          ⭐ 设为特关
        </button>
      </div>
    `).join("");

    container.querySelectorAll(".btn-quick-add-care").forEach(btn => {
      btn.addEventListener("click", async () => {
        const uid = btn.getAttribute("data-uid");
        const nick = btn.getAttribute("data-nick") || "";
        SFX.playChime();
        const res = await ApiClient.manageQzoneCare("add", uid, nick, "WebUI 快捷添加为特别关注");
        if (res?.success) {
          this.cachedData = res;
          this.renderAll();
        } else {
          alert(`添加失败: ${res?.error || "未知错误"}`);
        }
      });
    });
  },

  renderPatrolLogs() {
    const container = document.getElementById("qzone-patrol-logs");
    if (!container) return;

    const logs = this.cachedData?.recent_logs || [];
    if (logs.length === 0) {
      container.innerHTML = `<div class="empty-placeholder">还没有空间巡检足迹记录（每日 08:30 / 14:30 / 20:30 档期自动执行）。</div>`;
      return;
    }

    container.innerHTML = logs.map(l => `
      <div class="wl-bot-card">
        <div class="wl-bot-info">
          <span class="wl-bot-icon">🔭</span>
          <div>
            <div class="wl-bot-name">[${this._escape(l.time_str)}] 档期: ${this._escape(l.slot)}</div>
            <div class="wl-bot-meta">${this._escape(l.summary)}</div>
          </div>
        </div>
      </div>
    `).join("");
  },

  _escape(str) {
    return String(str ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
};
