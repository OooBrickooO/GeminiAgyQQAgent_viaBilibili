/**
 * webui/static/js/affection_view.js — 好感度羁绊体系与社交画像视图
 */
const AffectionView = {
  allUsers: [],

  init() {
    const searchInput = document.getElementById("affection-search-input");
    if (searchInput) {
      searchInput.addEventListener("input", (e) => {
        this.filterAndRender(e.target.value.trim().toLowerCase());
      });
    }

    const btnDelta = document.getElementById("btn-aff-quick-delta");
    const btnSet = document.getElementById("btn-aff-quick-set");
    const runQuick = async (action) => {
      const uid = document.getElementById("aff-quick-uid")?.value?.trim();
      const val = parseFloat(document.getElementById("aff-quick-val")?.value);
      const reason = document.getElementById("aff-quick-reason")?.value?.trim() || "WebUI 主人手动调节";
      if (!uid || Number.isNaN(val)) {
        alert("请输入有效的目标 QQ 号和数字分值！");
        return;
      }
      await this._execManage(action, uid, val, reason);
    };
    if (btnDelta) btnDelta.addEventListener("click", () => runQuick("delta"));
    if (btnSet) btnSet.addEventListener("click", () => runQuick("set"));
  },

  async _execManage(action, userId, value = 0, reason = "") {
    if (typeof SFX !== "undefined") SFX.playChime();
    const res = await ApiClient.manageAffection(action, userId, value, reason);
    if (res?.success) {
      this.updateView(res);
    } else {
      alert(`操作失败: ${res?.error || "未知错误"}`);
    }
  },

  updateView(affData) {
    if (!affData || !affData.success) return;

    this.allUsers = affData.users || [];
    const counts = affData.tier_counts || {};

    // 更新各阶梯总数徽标
    const elBff = document.getElementById("count-bff");
    const elFriend = document.getElementById("count-friend");
    const elAcq = document.getElementById("count-acq");
    const elPasserby = document.getElementById("count-passerby");
    const elBlack = document.getElementById("count-blacklist");
    const elPendingBadge = document.getElementById("aff-pending-badge");
    const elPendingCnt = document.getElementById("count-aff-pending");

    if (elBff) elBff.textContent = counts.best_friend || 0;
    if (elFriend) elFriend.textContent = counts.friend || 0;
    if (elAcq) elAcq.textContent = counts.acquaintance || 0;
    if (elPasserby) elPasserby.textContent = counts.passerby || 0;
    if (elBlack) elBlack.textContent = counts.blacklist || 0;
    const pendingCnt = affData.pending_requests_count || 0;
    if (elPendingBadge && elPendingCnt) {
      elPendingCnt.textContent = pendingCnt;
      elPendingBadge.style.display = pendingCnt > 0 ? "inline-flex" : "none";
    }

    const currentSearch = document.getElementById("affection-search-input")?.value?.trim()?.toLowerCase() || "";
    this.filterAndRender(currentSearch);
  },

  _escape(str) {
    return String(str ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  },

  filterAndRender(filterText = "") {
    const container = document.getElementById("affection-users-grid");
    if (!container) return;

    let filtered = this.allUsers;
    if (filterText) {
      filtered = this.allUsers.filter(u =>
        u.user_id.toLowerCase().includes(filterText) ||
        u.nickname.toLowerCase().includes(filterText)
      );
    }

    if (filtered.length === 0) {
      container.innerHTML = `
        <div style="grid-column: 1 / -1; text-align: center; padding: 2.5rem; color: #6b7280;">
          <p>未找到符合条件的用户画像</p>
        </div>
      `;
      return;
    }

    container.innerHTML = filtered.map(u => {
      const uidEsc = this._escape(u.user_id);
      const nickEsc = this._escape(u.nickname);
      const avatarUrl = `https://q1.qlogo.cn/g?b=qq&nk=${uidEsc}&s=100`;
      const masterIcon = u.is_master ? `<span class="master-crown" title="主人">👑</span>` : "";
      const gateIcon = u.can_promote_bff ? "🔓 已达挚友硬锁" : "🔒 挚友硬锁锁定";
      const gateBg = u.can_promote_bff ? "rgba(16, 185, 129, 0.15)" : "rgba(239, 68, 68, 0.1)";
      const calibLabel = u.is_calibrated ? "" : " · 🌱初识观察中";

      const req = u.pending_request;
      const reqHtml = req ? (() => {
        const dVal = Number(req.proposed_delta || 0);
        const sign = dVal > 0 ? "+" : "";
        return `
          <div style="padding:0.55rem 0.7rem;border-radius:0.45rem;background:rgba(244,114,182,0.14);border:1px solid rgba(244,114,182,0.45);display:flex;flex-direction:column;gap:0.4rem;">
            <div style="font-size:0.78rem;color:#fbcfe8;">
              <b>💌 弥酱申请调分 (${sign}${dVal.toFixed(1)}):</b> ${this._escape(req.reason || "")}
            </div>
            <div style="display:flex;gap:0.45rem;">
              <button class="btn btn-sm btn-primary btn-aff-act" data-act="approve_request" data-uid="${uidEsc}">✅ 批准执行</button>
              <button class="btn btn-sm btn-outline btn-aff-act" data-act="reject_request" data-uid="${uidEsc}">✖ 驳回</button>
            </div>
          </div>
        `;
      })() : "";

      const controlsHtml = (!u.is_master && !u.is_bot) ? `
        <div style="display:flex;flex-direction:column;gap:0.45rem;padding-top:0.35rem;border-top:1px dashed rgba(255,255,255,0.1);">
          <div style="display:flex;flex-wrap:wrap;gap:0.35rem;align-items:center;">
            <button class="btn btn-sm btn-outline btn-aff-act" data-act="pct_5" data-val="1" data-uid="${uidEsc}" title="好感 +5%">+5%</button>
            <button class="btn btn-sm btn-outline btn-aff-act" data-act="pct_5" data-val="-1" data-uid="${uidEsc}" title="好感 -5%">-5%</button>
            <button class="btn btn-sm btn-outline btn-aff-act" data-act="delta_resonance" data-val="1" data-uid="${uidEsc}" title="高光共鸣 +1">✨+1</button>
            <button class="btn btn-sm btn-outline btn-aff-act" data-act="delta_resonance" data-val="-1" data-uid="${uidEsc}" title="高光共鸣 -1">✨-1</button>
            <button class="btn btn-sm btn-outline btn-aff-act" data-act="toggle_bff" data-uid="${uidEsc}" style="${u.master_approved_bff ? 'border-color:#f472b6;color:#f472b6;' : ''}" title="切换主人破格挚友授信">
              ${u.master_approved_bff ? "👑已授信挚友" : "👑授信挚友"}
            </button>
          </div>
          <div style="display:flex;gap:0.35rem;align-items:center;">
            <input type="number" step="0.5" class="search-input aff-card-input" data-uid="${uidEsc}" placeholder="分值 (±增减 / 设定)" style="flex:1;padding:0.3rem 0.55rem;font-size:0.78rem;">
            <button class="btn btn-sm btn-primary btn-aff-custom" data-act="delta" data-uid="${uidEsc}">增减</button>
            <button class="btn btn-sm btn-outline btn-aff-custom" data-act="set" data-uid="${uidEsc}">设定</button>
          </div>
        </div>
      ` : "";

      return `
        <div class="user-card" style="border-left: 3px solid ${u.badge_color};">
          <div class="user-card-header">
            <img class="user-avatar" src="${avatarUrl}" onerror="this.src='https://q1.qlogo.cn/g?b=qq&nk=10001&s=100'" alt="${nickEsc}">
            <div class="user-title-group">
              <div class="user-name">
                ${masterIcon}
                <span>${nickEsc}</span>
              </div>
              <div class="user-qq">QQ: ${uidEsc} · 相识 ${u.days_known} 天${calibLabel}</div>
            </div>
            <span class="user-tier-badge" style="background: ${u.badge_color}22; color: ${u.badge_color}; border: 1px solid ${u.badge_color}44;">
              ${u.tier_name}
            </span>
          </div>

          <div class="user-stats-row">
            <div class="user-stat-col">
              <span class="stat-label-tiny">羁绊等级</span>
              <span class="stat-val-bold">Lv.${u.level}</span>
            </div>
            <div class="user-stat-col">
              <span class="stat-label-tiny">好感分</span>
              <span class="stat-val-bold" style="color: ${u.badge_color}">${u.affection}</span>
            </div>
            <div class="user-stat-col">
              <span class="stat-label-tiny">互动频次</span>
              <span class="stat-val-bold">${u.interaction_count} 次</span>
            </div>
            <div class="user-stat-col">
              <span class="stat-label-tiny">高光共鸣</span>
              <span class="stat-val-bold" style="color: #f472b6;">${u.high_resonance_count} 次</span>
            </div>
          </div>

          <div class="gate-info-box" style="background: ${gateBg};">
            <b>${gateIcon}:</b> ${this._escape(u.gate_reason)}
          </div>
          ${reqHtml}
          ${controlsHtml}
        </div>
      `;
    }).join("");

    container.querySelectorAll(".btn-aff-act").forEach(btn => {
      btn.addEventListener("click", async () => {
        const act = btn.getAttribute("data-act");
        const uid = btn.getAttribute("data-uid");
        const val = parseFloat(btn.getAttribute("data-val") || "0");
        await this._execManage(act, uid, val, `WebUI 操作 (${act})`);
      });
    });

    container.querySelectorAll(".btn-aff-custom").forEach(btn => {
      btn.addEventListener("click", async () => {
        const act = btn.getAttribute("data-act");
        const uid = btn.getAttribute("data-uid");
        const inputEl = container.querySelector(`.aff-card-input[data-uid="${uid}"]`);
        const val = parseFloat(inputEl?.value);
        if (Number.isNaN(val)) {
          alert("请先在输入框填写数字分值！");
          return;
        }
        await this._execManage(act, uid, val, `WebUI 自定义${act === "set" ? "设定" : "调分"}`);
      });
    });
  }
};

