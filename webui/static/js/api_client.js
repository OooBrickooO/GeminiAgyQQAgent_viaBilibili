/**
 * webui/static/js/api_client.js — 后端 REST API 与 WebSocket 全双工流客户端
 */
const ApiClient = {
  baseUrl: window.location.origin,
  ws: null,
  wsConnected: false,
  wsReconnectTimer: null,
  messageListeners: [],

  async _get(endpoint, params = {}) {
    const url = new URL(endpoint, this.baseUrl);
    Object.keys(params).forEach(key => {
      if (params[key] !== undefined && params[key] !== null && params[key] !== "") {
        url.searchParams.append(key, params[key]);
      }
    });

    try {
      const response = await fetch(url.toString(), {
        method: "GET",
        headers: { "Accept": "application/json" }
      });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }
      return await response.json();
    } catch (err) {
      console.error(`API请求失败 [${endpoint}]:`, err);
      return { success: false, error: err.message };
    }
  },

  async getStatus() {
    return await this._get("/api/status");
  },

  async getMood() {
    return await this._get("/api/mood");
  },

  async getAffection() {
    return await this._get("/api/affection");
  },

  async manageAffection(action, userId, value = 0, reason = "") {
    return await this._get("/api/affection/manage", {
      action,
      user_id: userId,
      value,
      reason
    });
  },

  async getPermanentFacts(category = "all", status = "all", query = "", limit = 100) {
    return await this._get("/api/memory/facts", { category, status, query, limit });
  },

  async deletePermanentFact(factId) {
    return await this._get("/api/memory/facts/delete", { id: factId });
  },

  async searchMemory(query, targetId = "auto") {
    return await this._get("/api/memory/search", { q: query, target_id: targetId });
  },

  async getMidTermMemories() {
    return await this._get("/api/memory/mid_term");
  },

  async deleteMidTermMemory(memoryId, text = "") {
    return await this._get("/api/memory/mid_term/delete", { id: memoryId, text });
  },

  async getLogs(type = "all", lines = 100) {
    return await this._get("/api/logs", { type, lines });
  },

  async getMindGraph() {
    return await this._get("/api/graph");
  },

  async getWhitelist() {
    return await this._get("/api/whitelist");
  },

  async manageGroupWhitelist(action, groupId) {
    return await this._get("/api/whitelist/group", { action, group_id: groupId });
  },

  async manageBotWhitelist(action, target, name = "") {
    return await this._get("/api/whitelist/bot", { action, target, name });
  },

  async getQzoneCare() {
    return await this._get("/api/qzone/special_care");
  },

  async manageQzoneCare(action, userId, nickname = "", reason = "") {
    return await this._get("/api/qzone/special_care/manage", {
      action,
      user_id: userId,
      nickname,
      reason
    });
  },

  async getLowEffort() {
    return await this._get("/api/effort/low");
  },

  async manageLowEffort(action, chatId = "", label = "") {
    return await this._get("/api/effort/low/manage", {
      action,
      chat_id: chatId,
      label
    });
  },

  async getLibido() {
    return await this._get("/api/libido");
  },

  async manageLibido(params = {}) {
    return await this._get("/api/libido/manage", params);
  },

  // WebSocket 实时流管理
  connectWebSocket(onDataCallback) {
    if (onDataCallback) {
      this.messageListeners.push(onDataCallback);
    }

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/ws`;

    try {
      this.ws = new WebSocket(wsUrl);

      this.ws.onopen = () => {
        this.wsConnected = true;
        this.updateWsBadge(true);
        console.log("⚡ [WebSocket] 已建立与星见弥后端的实时全双工长连接！");
      };

      this.ws.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data);
          this.messageListeners.forEach(fn => fn(payload));
        } catch (e) {
          console.warn("WS 消息解析异常:", e);
        }
      };

      this.ws.onclose = () => {
        this.wsConnected = false;
        this.updateWsBadge(false);
        this._scheduleReconnect();
      };

      this.ws.onerror = () => {
        this.wsConnected = false;
        this.updateWsBadge(false);
      };
    } catch (_) {
      this._scheduleReconnect();
    }
  },

  _scheduleReconnect() {
    if (this.wsReconnectTimer) return;
    this.wsReconnectTimer = setTimeout(() => {
      this.wsReconnectTimer = null;
      this.connectWebSocket();
    }, 4000);
  },

  updateWsBadge(connected) {
    const badge = document.getElementById("ws-status-badge");
    if (!badge) return;
    if (connected) {
      badge.innerHTML = `<span class="ws-dot active"></span> <span>WS 实时推送: 极速在线</span>`;
      badge.className = "ws-badge ws-online";
    } else {
      badge.innerHTML = `<span class="ws-dot"></span> <span>WS 重连中 (切HTTP轮询)</span>`;
      badge.className = "ws-badge ws-fallback";
    }
  }
};
