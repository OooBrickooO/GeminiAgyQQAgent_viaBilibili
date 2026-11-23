/**
 * webui/static/js/deskpet_view.js — 8088 WebUI 星见弥桌宠与屏幕分析/CUA 控制面板
 */
const DeskPetView = {
  init() {
    const chkPeek = document.getElementById("chk-deskpet-proactive-peek");
    const chkGame = document.getElementById("chk-deskpet-allow-ingame");
    const chkPassFs = document.getElementById("chk-deskpet-pass-fullscreen");
    const chkCuaEn = document.getElementById("chk-deskpet-cua-enabled");
    const chkCuaCtrl = document.getElementById("chk-deskpet-cua-control");
    const selPerm = document.getElementById("sel-deskpet-cua-perm");
    const selRoam = document.getElementById("sel-deskpet-roam");
    const btnSaveIntervals = document.getElementById("btn-deskpet-save-intervals");
    const btnPeekNow = document.getElementById("btn-deskpet-peek-now");
    const btnSayTest = document.getElementById("btn-deskpet-say-test");

    const pushPrefs = async (params) => {
      const qs = new URLSearchParams({ action: "update_prefs", ...params }).toString();
      try {
        const res = await fetch(`/api/deskpet/manage?${qs}`, { method: "POST" }).then(r => r.json());
        if (res?.overview) this.render(res.overview);
      } catch (e) {
        console.warn("更新桌宠配置失败:", e);
      }
    };

    chkPeek?.addEventListener("change", () => pushPrefs({ proactive_peek: chkPeek.checked }));
    chkGame?.addEventListener("change", () => pushPrefs({ allow_in_game: chkGame.checked }));
    chkPassFs?.addEventListener("change", () => pushPrefs({ pass_when_fullscreen: chkPassFs.checked }));
    chkCuaEn?.addEventListener("change", () => pushPrefs({ cua_enabled: chkCuaEn.checked }));
    chkCuaCtrl?.addEventListener("change", () => pushPrefs({ cua_control: chkCuaCtrl.checked }));
    selPerm?.addEventListener("change", () => pushPrefs({ cua_permission: selPerm.value }));
    selRoam?.addEventListener("change", () => pushPrefs({ roam: selRoam.value }));

    btnSaveIntervals?.addEventListener("click", () => {
      const norm = document.getElementById("input-deskpet-normal-sec")?.value || "900";
      const game = document.getElementById("input-deskpet-game-sec")?.value || "240";
      const sc = document.getElementById("input-deskpet-scale")?.value || "1.4";
      const talkHk = document.getElementById("sel-deskpet-talk-hotkey")?.value || "LeftAlt*2";
      const talkMode = document.getElementById("sel-deskpet-talk-mode")?.value || "auto";
      const typeHk = document.getElementById("sel-deskpet-type-hotkey")?.value || "LeftCtrl*2";
      pushPrefs({
        normal_interval_sec: norm,
        game_interval_sec: game,
        scale: sc,
        talk_hotkey: talkHk,
        talk_mode: talkMode,
        type_hotkey: typeHk,
      });
    });

    document.getElementById("sel-deskpet-talk-hotkey")?.addEventListener("change", (e) => pushPrefs({ talk_hotkey: e.target.value }));
    document.getElementById("sel-deskpet-talk-mode")?.addEventListener("change", (e) => pushPrefs({ talk_mode: e.target.value }));
    document.getElementById("sel-deskpet-type-hotkey")?.addEventListener("change", (e) => pushPrefs({ type_hotkey: e.target.value }));

    btnPeekNow?.addEventListener("click", async () => {
      const tipEl = document.getElementById("deskpet-action-feedback");
      if (tipEl) tipEl.textContent = "📸 正在截取当前电脑屏幕并召唤弥酱吐槽...";
      try {
        const res = await fetch("/api/deskpet/manage?action=peek_now", { method: "POST" }).then(r => r.json());
        if (tipEl) tipEl.textContent = res?.message || res?.error || "已触发";
        if (res?.overview) this.render(res.overview);
      } catch (e) {
        if (tipEl) tipEl.textContent = "触发失败: " + e.message;
      }
    });

    btnSayTest?.addEventListener("click", async () => {
      const input = document.getElementById("input-deskpet-say-script");
      const emo = document.getElementById("sel-deskpet-say-emotion")?.value || "auto";
      const script = input?.value?.trim();
      if (!script) return;
      const tipEl = document.getElementById("deskpet-action-feedback");
      if (tipEl) tipEl.textContent = "🎙️ 正在合成 9D 语音并投递到桌宠气泡...";
      const qs = new URLSearchParams({ action: "say_test", script, emotion: emo }).toString();
      try {
        const res = await fetch(`/api/deskpet/manage?${qs}`, { method: "POST" }).then(r => r.json());
        if (tipEl) tipEl.textContent = res?.message || res?.error || "已发送";
      } catch (e) {
        if (tipEl) tipEl.textContent = "发送失败: " + e.message;
      }
    });

    document.querySelectorAll(".btn-deskpet-quick-act").forEach(btn => {
      btn.addEventListener("click", async () => {
        const act = btn.getAttribute("data-act");
        if (!act) return;
        const qs = new URLSearchParams({ action: "act_test", script: act }).toString();
        await fetch(`/api/deskpet/manage?${qs}`, { method: "POST" });
      });
    });
  },

  async refresh() {
    try {
      const data = await fetch("/api/deskpet").then(r => r.json());
      this.render(data);
    } catch (e) {
      console.warn("拉取桌宠状态失败:", e);
    }
  },

  render(d) {
    if (!d) return;
    const onlineBadge = document.getElementById("deskpet-online-badge");
    const winBadge = document.getElementById("deskpet-window-badge");
    const fgBadge = document.getElementById("deskpet-fg-badge");
    const gameBadge = document.getElementById("deskpet-game-badge");

    if (onlineBadge) {
      onlineBadge.textContent = d.online ? `🟢 在线 (${d.endpoint || '7797'})` : "⚪ 离线 (运行 DeskPet/start_pet.bat 启动)";
      onlineBadge.style.color = d.online ? "#4ade80" : "#94a3b8";
    }
    if (winBadge) {
      winBadge.textContent = d.pet_window_connected ? `${d.figure_name} · 透明穿透窗口已就绪` : "等待桌宠窗口连接";
    }
    if (fgBadge) {
      fgBadge.textContent = d.foreground_app ? `[${d.foreground_app}] ${d.foreground_title || ''}` : "暂无前台窗口数据";
    }
    if (gameBadge) {
      gameBadge.textContent = d.in_game ? "🎮 游戏局内实时监测中" : "💻 日常桌面陪伴中";
    }

    const setChk = (id, val) => { const el = document.getElementById(id); if (el) el.checked = !!val; };
    const setVal = (id, val) => { const el = document.getElementById(id); if (el && document.activeElement !== el) el.value = val; };

    setChk("chk-deskpet-proactive-peek", d.proactive_peek);
    setChk("chk-deskpet-allow-ingame", d.allow_in_game);
    setChk("chk-deskpet-pass-fullscreen", d.pass_when_fullscreen);
    setChk("chk-deskpet-cua-enabled", d.cua_enabled);
    setChk("chk-deskpet-cua-control", d.cua_control);
    setVal("sel-deskpet-cua-perm", d.cua_permission || "ask-once");
    setVal("sel-deskpet-roam", d.roam || "calm");
    setVal("input-deskpet-normal-sec", d.normal_interval_sec ?? 900);
    setVal("input-deskpet-game-sec", d.game_interval_sec ?? 240);
    setVal("input-deskpet-scale", d.scale ?? 1.4);
    setVal("sel-deskpet-talk-hotkey", d.asr_hotkey || "LeftAlt*2");
    setVal("sel-deskpet-talk-mode", d.asr_mode || "auto");
    setVal("sel-deskpet-type-hotkey", d.type_hotkey || "LeftCtrl*2");
  }
};
