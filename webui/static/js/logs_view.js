/**
 * webui/static/js/logs_view.js — 实时审计日志流控制视图
 */
const LogsView = {
  activeType: "all",
  autoScroll: true,

  init() {
    const buttons = document.querySelectorAll("#log-type-buttons .btn");
    buttons.forEach(btn => {
      btn.addEventListener("click", () => {
        buttons.forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        this.activeType = btn.getAttribute("data-type") || "all";
        this.refreshLogs();
      });
    });

    const refreshBtn = document.getElementById("btn-refresh-logs");
    if (refreshBtn) {
      refreshBtn.addEventListener("click", () => this.refreshLogs());
    }

    const chkAutoScroll = document.getElementById("chk-auto-scroll");
    if (chkAutoScroll) {
      chkAutoScroll.addEventListener("change", (e) => {
        this.autoScroll = e.target.checked;
      });
    }
  },

  async refreshLogs() {
    const res = await ApiClient.getLogs(this.activeType, 100);
    const titleEl = document.getElementById("log-file-title");
    const countEl = document.getElementById("log-lines-count");
    const terminalEl = document.getElementById("log-terminal-output");

    if (titleEl) titleEl.textContent = `logs/${res.file || 'all.log'}`;
    if (countEl) countEl.textContent = `${res.lines?.length || 0} 行`;

    if (terminalEl) {
      if (res.lines && res.lines.length > 0) {
        terminalEl.textContent = res.lines.join("\n");
      } else {
        terminalEl.textContent = "暂无匹配日志输出。";
      }

      if (this.autoScroll) {
        terminalEl.scrollTop = terminalEl.scrollHeight;
      }
    }
  }
};

