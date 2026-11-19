/**
 * webui/static/js/memory_view.js — L3 永久记忆库与 L2 温感记忆视图控制器
 */
const MemoryView = {
  activeCategory: "all",
  activeStatus: "active",
  categoryNames: {
    "user_fact": "👤 人物画像",
    "shared_promise": "🤝 专属约定",
    "group_meme": "🎉 群记忆/群梗",
    "milestone": "🏆 情感里程碑"
  },
  categoryColors: {
    "user_fact": "#3b82f6",
    "shared_promise": "#ec4899",
    "group_meme": "#10b981",
    "milestone": "#f59e0b"
  },

  init() {
    // 绑定分类过滤按钮
    const pills = document.querySelectorAll("#facts-category-pills .pill-btn");
    pills.forEach(btn => {
      btn.addEventListener("click", () => {
        pills.forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        this.activeCategory = btn.getAttribute("data-cat") || "all";
        this.refreshPermanentFacts();
      });
    });

    // 绑定状态下拉框
    const statusSelect = document.getElementById("facts-status-filter");
    if (statusSelect) {
      statusSelect.addEventListener("change", (e) => {
        this.activeStatus = e.target.value;
        this.refreshPermanentFacts();
      });
    }

    // 绑定 FTS5 检索测试按钮
    const searchBtn = document.getElementById("btn-run-fts-search");
    const searchInput = document.getElementById("fts-query-input");
    if (searchBtn && searchInput) {
      const doSearch = async () => {
        const q = searchInput.value.trim();
        if (!q) return;
        searchBtn.disabled = true;
        searchBtn.innerHTML = `<span>⏳ 检索中...</span>`;
        const res = await ApiClient.searchMemory(q);
        searchBtn.disabled = false;
        searchBtn.innerHTML = `<span class="icon">🔍</span> 毫秒检索测试`;

        const latencyBadge = document.getElementById("search-latency-badge");
        const resultBox = document.getElementById("fts-search-result-box");
        if (latencyBadge) {
          latencyBadge.textContent = `耗时: ${res.elapsed_ms || 0}ms`;
        }
        if (resultBox) {
          resultBox.style.display = "block";
          resultBox.textContent = res.result_text || "未检索到匹配记录。";
        }
      };

      searchBtn.addEventListener("click", doSearch);
      searchInput.addEventListener("keypress", (e) => {
        if (e.key === "Enter") doSearch();
      });
    }
  },

  _formatSoftenedText(rawText) {
    if (!rawText) return "";
    const escaped = String(rawText)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
    return escaped.replace(
      /〖已柔化:([^〗]+)〗/g,
      `<span class="softened-tag" title="该词条因防云端风控已做温和有痕柔化，弥酱调用时会感知此标记">🛡️〖已柔化:$1〗</span>`
    );
  },

  async refreshPermanentFacts() {
    const res = await ApiClient.getPermanentFacts(this.activeCategory, this.activeStatus);
    const container = document.getElementById("facts-list-container");
    if (!container) return;

    if (!res.success || !res.facts || res.facts.length === 0) {
      container.innerHTML = `
        <div style="grid-column: 1 / -1; text-align: center; padding: 2.5rem; color: #6b7280;">
          <p>当前分类下暂无已铭记的永久事实</p>
        </div>
      `;
      return;
    }

    container.innerHTML = res.facts.map(f => {
      const catCn = this.categoryNames[f.category] || f.category;
      const catColor = this.categoryColors[f.category] || "#60a5fa";
      const isSuperseded = f.status === "superseded";
      const supersededBadge = isSuperseded ? `<span style="color: #ef4444; font-size: 0.72rem;">[已迭代旧状态]</span>` : "";
      const factKeyTag = f.fact_key ? `<span class="fact-key-tag">键: ${f.fact_key}</span>` : "";

      return `
        <div class="fact-card ${isSuperseded ? 'superseded' : ''}" data-fact-id="${f.id}">
          <div class="fact-card-header">
            <div style="display:flex; align-items:center; gap:0.4rem; flex-wrap:wrap;">
              <span class="fact-cat-tag" style="background: ${catColor}22; color: ${catColor}; border: 1px solid ${catColor}44;">
                ${catCn}
              </span>
              ${factKeyTag}
              ${supersededBadge}
            </div>
            <button class="btn-delete-memory btn-delete-l3" data-id="${f.id}" title="手动删除该条 L3 永久记忆">
              🗑️ 删除
            </button>
          </div>

          <p class="fact-content">${this._formatSoftenedText(f.content)}</p>

          <div class="fact-footer">
            <span>对象: ${f.target_id} · #${f.id}</span>
            <span>铭记于: ${f.created_at || '早期历史'}</span>
          </div>
        </div>
      `;
    }).join("");

    container.querySelectorAll(".btn-delete-l3").forEach(btn => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const factId = btn.getAttribute("data-id");
        if (!factId) return;
        if (!window.confirm(`确定要从 L3 永久记忆库中彻底删除记忆 #${factId} 吗？`)) return;

        btn.disabled = true;
        btn.textContent = "删除中...";
        const delRes = await ApiClient.deletePermanentFact(factId);
        if (delRes && delRes.success) {
          if (window.SFX) SFX.playPulse();
          await this.refreshPermanentFacts();
          if (window.App) App.fetchAllData();
        } else {
          window.alert("删除失败: " + (delRes?.error || "未知错误"));
          btn.disabled = false;
          btn.textContent = "🗑️ 删除";
        }
      });
    });
  },

  async refreshMidTermMemories() {
    const res = await ApiClient.getMidTermMemories();
    const container = document.getElementById("mid-term-list-container");
    if (!container) return;

    if (!res.success || !res.memories || res.memories.length === 0) {
      container.innerHTML = `
        <div style="grid-column: 1 / -1; text-align: center; padding: 2.5rem; color: #6b7280;">
          <p>当前中程温感记忆库为空（对话或B站手账将自动沉淀）</p>
        </div>
      `;
      return;
    }

    container.innerHTML = res.memories.map((m, idx) => {
      const pct = Math.round(m.activation * 100);
      return `
        <div class="mid-card" data-mid-idx="${idx}">
          <div class="mid-card-header">
            <div style="display:flex; align-items:center; gap:0.45rem;">
              <span class="mid-temp-badge" style="background: ${m.temp_color}22; color: ${m.temp_color}; border: 1px solid ${m.temp_color}44;">
                ${m.temp_status}
              </span>
              <span class="mid-time-badge">对象: ${m.target_id} · ${m.time_ago}</span>
            </div>
            <button class="btn-delete-memory btn-delete-l2" data-id="${m.id || ''}" data-idx="${idx}" title="手动删除该条 L2 温感记忆">
              🗑️ 删除
            </button>
          </div>

          <p class="mid-content">${this._formatSoftenedText(m.text)}</p>

          <div class="mid-gauge-row">
            <div class="mid-gauge-meta">
              <span>艾宾浩斯激活度: <b>${m.activation}</b> (${pct}%)</span>
              <span>重提强化: ${m.recall_count}次 · 半衰期: ${m.half_life_days}天</span>
            </div>
            <div class="mid-gauge-track">
              <div class="mid-gauge-fill" style="width: ${pct}%; background: ${m.temp_color};"></div>
            </div>
          </div>
        </div>
      `;
    }).join("");

    container.querySelectorAll(".btn-delete-l2").forEach(btn => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const memId = btn.getAttribute("data-id") || "";
        const idx = parseInt(btn.getAttribute("data-idx") || "0", 10);
        const memObj = res.memories[idx] || {};
        if (!window.confirm(`确定要手动删除这条 L2 中程温感记忆吗？\n\n「${(memObj.text || '').slice(0, 60)}」`)) return;

        btn.disabled = true;
        btn.textContent = "删除中...";
        const delRes = await ApiClient.deleteMidTermMemory(memId, memObj.text || "");
        if (delRes && delRes.success) {
          if (window.SFX) SFX.playPulse();
          await this.refreshMidTermMemories();
          if (window.App) App.fetchAllData();
        } else {
          window.alert("删除失败: " + (delRes?.error || "未知错误"));
          btn.disabled = false;
          btn.textContent = "🗑️ 删除";
        }
      });
    });
  }
};

