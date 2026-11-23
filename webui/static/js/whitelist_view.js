/**
 * webui/static/js/whitelist_view.js — 群聊活跃白名单与群 Bot 免压白名单管理视图
 */
const WhitelistView = {
  cachedData: null,
  lowEffortData: null,
  searchQuery: "",

  init() {
    const searchInput = document.getElementById("whitelist-group-search");
    if (searchInput) {
      searchInput.addEventListener("input", (e) => {
        this.searchQuery = (e.target.value || "").trim().toLowerCase();
        this.renderGroups();
      });
    }

    const btnAddGroup = document.getElementById("btn-add-whitelist-group");
    const inputAddGroup = document.getElementById("input-add-whitelist-group");
    if (btnAddGroup && inputAddGroup) {
      const handleAddGroup = async () => {
        const gid = (inputAddGroup.value || "").trim();
        if (!gid) return;
        SFX.playPulse();
        const res = await ApiClient.manageGroupWhitelist("add", gid);
        if (res?.success) {
          inputAddGroup.value = "";
          this.cachedData = res;
          this.renderAll();
        } else if (res?.error) {
          alert(`添加失败: ${res.error}`);
        }
      };
      btnAddGroup.addEventListener("click", handleAddGroup);
      inputAddGroup.addEventListener("keydown", (e) => {
        if (e.key === "Enter") handleAddGroup();
      });
    }

    const btnAddBot = document.getElementById("btn-add-bot-whitelist");
    const inputBotTarget = document.getElementById("input-bot-whitelist-target");
    const inputBotName = document.getElementById("input-bot-whitelist-name");
    if (btnAddBot && inputBotTarget) {
      const handleAddBot = async () => {
        const target = (inputBotTarget.value || "").trim();
        const name = (inputBotName?.value || "").trim();
        if (!target) return;
        SFX.playPulse();
        const res = await ApiClient.manageBotWhitelist("add", target, name);
        if (res?.success) {
          inputBotTarget.value = "";
          if (inputBotName) inputBotName.value = "";
          this.cachedData = res;
          this.renderAll();
        } else if (res?.error) {
          alert(`添加群 Bot 失败: ${res.error}`);
        }
      };
      btnAddBot.addEventListener("click", handleAddBot);
      inputBotTarget.addEventListener("keydown", (e) => {
        if (e.key === "Enter") handleAddBot();
      });
    }

    const btnEnableLow = document.getElementById("btn-enable-low-effort");
    const inputLowCid = document.getElementById("input-low-effort-chatid");
    const inputLowLabel = document.getElementById("input-low-effort-label");
    if (btnEnableLow && inputLowCid) {
      const handleAddLow = async () => {
        const cid = (inputLowCid.value || "").trim();
        const lbl = (inputLowLabel?.value || "").trim();
        if (!cid) return;
        SFX.playPulse();
        const res = await ApiClient.manageLowEffort("enable", cid, lbl);
        if (res?.success) {
          inputLowCid.value = "";
          if (inputLowLabel) inputLowLabel.value = "";
          this.lowEffortData = res;
          this.renderLowEffort();
        } else if (res?.error) {
          alert(`开启局部 Low 失败: ${res.error}`);
        }
      };
      btnEnableLow.addEventListener("click", handleAddLow);
      inputLowCid.addEventListener("keydown", (e) => {
        if (e.key === "Enter") handleAddLow();
      });
    }

    const btnClearAllLow = document.getElementById("btn-clear-all-low-effort");
    if (btnClearAllLow) {
      btnClearAllLow.addEventListener("click", async () => {
        SFX.playPulse();
        const res = await ApiClient.manageLowEffort("clear_all");
        if (res?.success) {
          this.lowEffortData = res;
          this.renderLowEffort();
        }
      });
    }
  },

  async refresh() {
    const [res, lowRes] = await Promise.all([
      ApiClient.getWhitelist(),
      ApiClient.getLowEffort()
    ]);
    if (res?.success) {
      this.cachedData = res;
      this.renderAll();
    }
    if (lowRes?.success) {
      this.lowEffortData = lowRes;
      this.renderLowEffort();
    }
  },

  renderAll() {
    if (!this.cachedData) return;

    const countActiveEl = document.getElementById("wl-count-active");
    const countTotalEl = document.getElementById("wl-count-total");
    const countBotsEl = document.getElementById("wl-count-bots");
    const napcatBadgeEl = document.getElementById("wl-napcat-status");

    if (countActiveEl) countActiveEl.textContent = this.cachedData.whitelisted_count ?? 0;
    if (countTotalEl) countTotalEl.textContent = this.cachedData.total_groups_count ?? 0;
    if (countBotsEl) countBotsEl.textContent = (this.cachedData.bot_whitelist || []).length;
    if (napcatBadgeEl) {
      if (this.cachedData.napcat_online) {
        napcatBadgeEl.textContent = "OneBot 群列表已同步";
        napcatBadgeEl.className = "card-tag tag-online";
      } else {
        napcatBadgeEl.textContent = "本地配置模式 (OneBot 离线)";
        napcatBadgeEl.className = "card-tag";
      }
    }

    this.renderGroups();
    this.renderBots();
  },

  renderGroups() {
    const container = document.getElementById("whitelist-groups-grid");
    if (!container) return;

    const groups = (this.cachedData?.groups || []).filter(g => {
      if (!this.searchQuery) return true;
      return (
        String(g.group_id).toLowerCase().includes(this.searchQuery) ||
        String(g.group_name || "").toLowerCase().includes(this.searchQuery)
      );
    });

    if (groups.length === 0) {
      container.innerHTML = `<div class="empty-placeholder">暂无匹配的 QQ 群记录，可在上方输入群号手动添加。</div>`;
      return;
    }

    container.innerHTML = groups.map(g => {
      const isAllowed = Boolean(g.whitelisted);
      const memberStr = g.member_count > 0 ? `${g.member_count}/${g.max_member_count || '-'} 人` : "手动登记群号";
      const recentStr = g.recent_msg_count > 0 ? `近期滑窗 ${g.recent_msg_count} 条` : "暂无近期缓存";
      return `
        <div class="wl-group-card ${isAllowed ? 'wl-active' : 'wl-inactive'}">
          <div class="wl-group-main">
            <div class="wl-group-avatar">${isAllowed ? '🟢' : '⚪'}</div>
            <div class="wl-group-info">
              <div class="wl-group-title-row">
                <span class="wl-group-name" title="${this._escape(g.group_name)}">${this._escape(g.group_name)}</span>
                <span class="wl-status-pill ${isAllowed ? 'pill-on' : 'pill-off'}">
                  ${isAllowed ? '已启用冒泡与响应' : '静默旁观'}
                </span>
              </div>
              <div class="wl-group-meta">
                <span class="mono-id">群号: ${this._escape(g.group_id)}</span>
                <span>·</span>
                <span>${memberStr}</span>
                <span>·</span>
                <span>${recentStr}</span>
              </div>
            </div>
          </div>
          <div class="wl-group-actions">
            <label class="wl-switch" title="点击切换该群活跃白名单状态">
              <input type="checkbox" class="chk-group-toggle" data-gid="${this._escape(g.group_id)}" ${isAllowed ? 'checked' : ''}>
              <span class="wl-slider"></span>
            </label>
          </div>
        </div>
      `;
    }).join("");

    container.querySelectorAll(".chk-group-toggle").forEach(chk => {
      chk.addEventListener("change", async (e) => {
        const gid = e.target.getAttribute("data-gid");
        SFX.playChime();
        const res = await ApiClient.manageGroupWhitelist("toggle", gid);
        if (res?.success) {
          this.cachedData = res;
          this.renderAll();
        } else {
          alert(`切换白名单状态失败: ${res?.error || '未知错误'}`);
          this.refresh();
        }
      });
    });
  },

  renderBots() {
    const container = document.getElementById("bot-whitelist-grid");
    if (!container) return;

    const bots = this.cachedData?.bot_whitelist || [];
    if (bots.length === 0) {
      container.innerHTML = `<div class="empty-placeholder">当前尚未登记任何免压群 Bot。</div>`;
      return;
    }

    container.innerHTML = bots.map(b => `
      <div class="wl-bot-card">
        <div class="wl-bot-info">
          <span class="wl-bot-icon">🤖</span>
          <div>
            <div class="wl-bot-name">${this._escape(b.name)}</div>
            <div class="wl-bot-meta">QQ: ${this._escape(b.qq || '按昵称匹配')} · 添加于 ${this._escape(b.added_at)}</div>
          </div>
        </div>
        <button class="btn btn-sm btn-outline btn-remove-bot" data-target="${this._escape(b.qq || b.key)}">
          移出
        </button>
      </div>
    `).join("");

    container.querySelectorAll(".btn-remove-bot").forEach(btn => {
      btn.addEventListener("click", async () => {
        const target = btn.getAttribute("data-target");
        SFX.playPulse();
        const res = await ApiClient.manageBotWhitelist("remove", target);
        if (res?.success) {
          this.cachedData = res;
          this.renderAll();
        } else {
          alert(`移出失败: ${res?.error || '未知错误'}`);
        }
      });
    });
  },

  renderLowEffort() {
    const container = document.getElementById("low-effort-channels-grid");
    const countEl = document.getElementById("low-effort-enabled-count");
    if (countEl) countEl.textContent = this.lowEffortData?.enabled_count ?? 0;
    if (!container) return;

    const channels = this.lowEffortData?.channels || [];
    if (channels.length === 0) {
      container.innerHTML = `<div class="empty-placeholder">暂无可选对话通道，可在上方输入 private_QQ 或 group_群号 手动开启。</div>`;
      return;
    }

    container.innerHTML = channels.map(ch => {
      const isLow = Boolean(ch.enabled);
      const icon = ch.type === "private" ? "💌" : "💬";
      return `
        <div class="wl-group-card ${isLow ? 'wl-active' : 'wl-inactive'}">
          <div class="wl-group-main">
            <div class="wl-group-avatar">${isLow ? '⚡' : icon}</div>
            <div class="wl-group-info">
              <div class="wl-group-title-row">
                <span class="wl-group-name" title="${this._escape(ch.label)}">${this._escape(ch.label)}</span>
                <span class="wl-status-pill ${isLow ? 'pill-on' : 'pill-off'}">
                  ${isLow ? '局部强制 Low' : '默认 Medium/High'}
                </span>
              </div>
              <div class="wl-group-meta">
                <span class="mono-id">通道: ${this._escape(ch.chat_id)}</span>
                ${isLow && ch.updated_at && ch.updated_at !== '-' ? `<span>·</span><span>开启于 ${this._escape(ch.updated_at)}</span>` : ''}
              </div>
            </div>
          </div>
          <div class="wl-group-actions">
            <label class="wl-switch" title="仅切换当前对话通道的局部 Low 档">
              <input type="checkbox" class="chk-low-effort-toggle" data-cid="${this._escape(ch.chat_id)}" data-label="${this._escape(ch.label)}" ${isLow ? 'checked' : ''}>
              <span class="wl-slider"></span>
            </label>
          </div>
        </div>
      `;
    }).join("");

    container.querySelectorAll(".chk-low-effort-toggle").forEach(chk => {
      chk.addEventListener("change", async (e) => {
        const cid = e.target.getAttribute("data-cid");
        const lbl = e.target.getAttribute("data-label") || "";
        SFX.playChime();
        const res = await ApiClient.manageLowEffort("toggle", cid, lbl);
        if (res?.success) {
          this.lowEffortData = res;
          this.renderLowEffort();
        } else {
          alert(`切换局部 Low 开关失败: ${res?.error || '未知错误'}`);
          this.refresh();
        }
      });
    });
  },

  _escape(str) {
    return String(str ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
};
