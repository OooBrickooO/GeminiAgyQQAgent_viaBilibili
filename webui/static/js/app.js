/**
 * webui/static/js/app.js — 前端主控制器与生命周期管理 (全功能增强版)
 */
const App = {
  activeTab: "tab-overview",
  autoRefresh: true,
  refreshIntervalMs: 5000,
  timerId: null,

  async init() {
    console.log("🌟 星见弥 (Hoshimi Neko) 认知与情绪可视化仪表盘初始化中...");

    // 1. 初始化各视觉、音效与功能模块
    Starfield.init();
    SFX.init();
    MoodView.initCharts();
    AffectionView.init();
    MemoryView.init();
    WhitelistView.init();
    QzoneCareView.init();
    LibidoView.init();
    if (typeof DeskPetView !== "undefined") DeskPetView.init();
    LogsView.init();
    GraphView.init();

    // 2. 绑定选项卡切换事件
    const tabs = document.querySelectorAll(".tab-item");
    tabs.forEach(tab => {
      tab.addEventListener("click", () => {
        tabs.forEach(t => t.classList.remove("active"));
        tab.classList.add("active");
        this.activeTab = tab.getAttribute("data-tab");

        document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
        const targetPanel = document.getElementById(this.activeTab);
        if (targetPanel) {
          targetPanel.classList.add("active");
        }

        // 播放切换科幻音效
        SFX.playChime();

        // 切换后特定视图专属刷新/重绘
        if (this.activeTab === "tab-overview") {
          setTimeout(() => {
            MoodView.radarChart?.resize();
            MoodView.ekgChart?.resize();
          }, 60);
        } else if (this.activeTab === "tab-graph") {
          setTimeout(() => {
            GraphView.chart?.resize();
            GraphView.refreshGraph();
          }, 80);
        } else if (this.activeTab === "tab-memory-l3") {
          MemoryView.refreshPermanentFacts();
        } else if (this.activeTab === "tab-memory-l2") {
          MemoryView.refreshMidTermMemories();
        } else if (this.activeTab === "tab-whitelist") {
          WhitelistView.refresh();
        } else if (this.activeTab === "tab-qzone-care") {
          QzoneCareView.refresh();
        } else if (this.activeTab === "tab-libido") {
          setTimeout(() => LibidoView.refresh(), 60);
        } else if (this.activeTab === "tab-deskpet") {
          if (typeof DeskPetView !== "undefined") DeskPetView.refresh();
        } else if (this.activeTab === "tab-logs") {
          LogsView.refreshLogs();
        }
      });
    });

    // 3. 绑定顶部刷新控制
    const btnAuto = document.getElementById("btn-toggle-auto-refresh");
    const autoText = document.getElementById("auto-refresh-text");
    if (btnAuto) {
      btnAuto.addEventListener("click", () => {
        this.autoRefresh = !this.autoRefresh;
        SFX.playPulse();
        if (this.autoRefresh) {
          autoText.textContent = "自动刷新: 开 (5s)";
          btnAuto.classList.add("btn-outline");
          this.startPolling();
        } else {
          autoText.textContent = "自动刷新: 暂停";
          this.stopPolling();
        }
      });
    }

    const btnManual = document.getElementById("btn-manual-refresh");
    if (btnManual) {
      btnManual.addEventListener("click", () => {
        SFX.playPulse();
        this.fetchAllData();
      });
    }

    // 4. 初次拉取全量数据
    await this.fetchAllData();

    // 5. 启动 WebSocket 实时推送长连接与保底轮询
    ApiClient.connectWebSocket((wsData) => this.onWebSocketMessage(wsData));
    this.startPolling();
  },

  onWebSocketMessage(payload) {
    if (!payload) return;
    // 收到 WebSocket 毫秒级推送时实时更新
    if (payload.type === "state_push") {
      if (payload.status) this.updateGlobalHeader(payload.status, payload.mood);
      if (payload.mood) {
        this.updateStatsCards(payload.status, payload.mood);
        MoodView.updateView(payload.mood);
      }
    }
  },

  async fetchAllData() {
    try {
      const [statusRes, moodRes, affRes] = await Promise.all([
        ApiClient.getStatus(),
        ApiClient.getMood(),
        ApiClient.getAffection()
      ]);

      // 更新顶部全局状态
      this.updateGlobalHeader(statusRes, moodRes);

      // 概览核心指标保持同步
      this.updateStatsCards(statusRes, moodRes);

      // 分发至各专属视图
      MoodView.updateView(moodRes);
      AffectionView.updateView(affRes);

      if (this.activeTab === "tab-graph") {
        GraphView.refreshGraph();
      } else if (this.activeTab === "tab-memory-l3") {
        MemoryView.refreshPermanentFacts();
      } else if (this.activeTab === "tab-memory-l2") {
        MemoryView.refreshMidTermMemories();
      } else if (this.activeTab === "tab-whitelist") {
        WhitelistView.refresh();
      } else if (this.activeTab === "tab-deskpet") {
        if (typeof DeskPetView !== "undefined") DeskPetView.refresh();
      } else if (this.activeTab === "tab-logs") {
        LogsView.refreshLogs();
      }
    } catch (err) {
      console.warn("拉取聚合数据异常:", err);
    }
  },

  updateGlobalHeader(statusRes, moodRes) {
    if (!statusRes?.success) return;

    const b = statusRes.bot_info || {};
    const m = statusRes.mood_summary || {};

    const timeEl = document.getElementById("sys-time");
    const uptimeEl = document.getElementById("sys-uptime");
    const moodBadgeEl = document.getElementById("sys-mood-badge");

    if (timeEl) timeEl.textContent = b.server_time || "";
    if (uptimeEl) uptimeEl.textContent = `运行时长: ${b.uptime_human || ""}`;
    if (moodBadgeEl) moodBadgeEl.textContent = `心境: ${m.current_mood?.toFixed(1) || '--'} · ${m.mood_label || ''}`;
  },

  updateStatsCards(statusRes, moodRes) {
    if (!statusRes?.success) return;

    const stats = statusRes.stats || {};
    const mood = moodRes?.mood || {};

    const moodScoreEl = document.getElementById("stat-mood-score");
    const factsActiveEl = document.getElementById("stat-facts-active");
    const factsSupersededEl = document.getElementById("stat-facts-superseded");
    const midMemoriesEl = document.getElementById("stat-mid-memories");
    const chatArchiveEl = document.getElementById("stat-chat-archive");
    const trackedUsersEl = document.getElementById("stat-tracked-users");

    if (moodScoreEl && mood.current !== undefined) {
      moodScoreEl.textContent = mood.current.toFixed(1);
    }
    if (factsActiveEl) factsActiveEl.textContent = stats.permanent_facts_active ?? 0;
    if (factsSupersededEl) factsSupersededEl.textContent = `${stats.permanent_facts_superseded ?? 0} 条已迭代历史事实`;
    if (midMemoriesEl) midMemoriesEl.textContent = stats.mid_term_memories ?? 0;
    if (chatArchiveEl) chatArchiveEl.textContent = stats.chat_archive_messages ?? 0;
    if (trackedUsersEl) trackedUsersEl.textContent = `已追踪 ${stats.user_profiles_tracked ?? 0} 位社交对象`;
  },

  startPolling() {
    this.stopPolling();
    this.timerId = setInterval(() => {
      // 若 WebSocket 正常在线，轮询放缓至低频兜底 (15s)，减少网络负荷
      if (this.autoRefresh && !ApiClient.wsConnected) {
        this.fetchAllData();
      }
    }, this.refreshIntervalMs);
  },

  stopPolling() {
    if (this.timerId) {
      clearInterval(this.timerId);
      this.timerId = null;
    }
  }
};

// 页面 DOM 加载完成后启动
document.addEventListener("DOMContentLoaded", () => {
  App.init();
});
