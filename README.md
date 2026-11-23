<div align="center">

<img src="DeskPet/web/mi/avatar.png" width="180" alt="星见弥 Hoshimi Mi" />

# GeminiAgyQQAgent — 星见弥（弥酱）数字生命 Agent

**基于 OneBot v11 + MCP + Antigravity CLI (`agy`) 的全生态 QQ 社交、B 站冲浪与桌面伴侣智能体**

[![Architecture & Deployment Guide](https://img.shields.io/badge/Docs-Status.md-blue.svg)](Status.md)

</div>

---

## ✨ 这是什么项目？

`GeminiAgyQQAgent` 是一个围绕“真实社交人格与日常陪伴感”构建的常驻数字生命系统。她不仅能在 QQ 群聊与私聊中按人类对话节律插话、连发气泡、记仇/记挂，还能在 Windows 桌面上以 **透明桌宠（DeskPet）** 的形态陪你看屏幕、爬到窗口顶边发呆、用日语真声配合中文气泡和你聊天，并在后台自主刷 B 站、逛 QQ 空间、写日常日记。

> [!NOTE]
> **想了解系统架构、模块清单、排错避坑，或准备自己部署 / 用 AI 改成单机版？**
> 👉 请直接阅读 **[Status.md](Status.md)**（内含完整的默认部署拓扑说明、端口汇总、单机改造要点以及 V3–V5.4.4 演进记录）。

---

## 🌟 核心能力一览

- **拟真 QQ 对话大脑**：消息碎片防抖合并（`3.0s` 统一节律 + 打断重跑 + 思考期消息沉淀）、社交熵动态话题压力、群聊渐进软上限与防自言自语闭环，配合异步队列实现“真人式多条短气泡”连发。
- **Desktop Pet 桌面伴侣 (`DeskPet/`)**：基于 Electron + 无钩子 Win32 方案，支持任务栏/非最大化窗口顶边坐立攀爬、物理拎起摔晕、二次元矢量漫符、**「中文气泡 + 日语配音 (`GPT-SoVITS-v5`)」**、主动看屏吐槽，以及带 2 秒人类让位锁的 CUA 屏幕/键鼠协助；桌宠日志独立输出至 `logs/deskpet.log` 不污染主控制台。
- **7 维动态情绪与三层记忆**：`happy / calm / sweet / proud / worry / sulky / jealous` 七维连续半衰期衰减状态机（接入 437 条静态复合词库与音色潜空间融合）+ L1 日终胶囊 / L2 中程挂念 (`pending_thoughts`) / L3 SQLite 永久核心档案，且群聊与私聊记忆物理隔离。
- **B 站与 QQ 空间自主生活**：B 站智能检索精看、故事板九宫格视觉读片、现代 WBI 协议 + Playwright 兜底收藏与夜间发动态；QQ 空间三档异步巡检、特别关注（特关）暖心关怀与“先策展后动笔”每日说说日记。
- **娱乐工具与 8088 控制台 (`webui/`)**：内置国际象棋、中国象棋（带高清开源棋子渲染）、五子棋、黑白棋、浅草寺猫猫签与网易云点歌台；自带 `0.0.0.0:8088` WebUI 面板可视化查看关系图谱、情绪雷达、记忆档案、白名单、通道 `low` 档开关与桌宠状态。

---

## 🚀 快速配置清单（上传版已全量脱敏）

本公开仓库已剔除个人账号、密钥、内网 IP、运行时数据及超大模型文件。拉取仓库后，按需补全以下配置即可运行（**也推荐直接把本仓库连同 `Status.md` 交给 AI 编程助手，按你的本机环境一键适配单机版**）：

1. **核心配置 ([config.py](config.py))**：替换或通过环境变量注入 `BOT_QQ`、`MASTER_QQ`、`NAPCAT_TOKEN`、`ONEBOT_GATEWAY_IP`、`TTS_PC_TAILSCALE_IP`、`DESKPET_PC_TAILSCALE_IP` 及 `AGY_EXECUTABLE`。
2. **MCP 服务入口 ([.agents/mcp_config.json](.agents/mcp_config.json))**：将 `qq-agent` 与 `bilibili-agent` 的 Python 解释器及脚本路径改为你自己的真实路径。
3. **群聊白名单 ([allowed_groups.json](allowed_groups.json))**：将模板中的 `"YOUR_GROUP_ID"` 替换为允许 Bot 发言的真实 QQ 群号（或启动后在 WebUI `8088` 中可视化添加）。
4. **桌宠客户端（可选，[DeskPet/config.json](DeskPet/config.json)）**：填写真实的 `token`（与 `NAPCAT_TOKEN` 对齐）、`brainCandidates` 与 `ttsCandidates`。
   - *注*：本地语音识别模型 `DeskPet/models/sensevoice-small-int8`（约 228MB）未随仓上传，不需要离线语音输入时可将 `DeskPet/config.json` 中 `asr.enabled` 设为 `false`。
5. **安装依赖与启动**：
   ```bash
   pip install -r requirements.txt
   python bot_daemon.py        # 启动主守护大脑与后台 Workers
   python webui_start.py       # (可选) 启动 8088 WebUI 管理面板
   ```
   Windows 桌宠端进入 `DeskPet/` 运行 `start_pet.bat`（或 `npm install && npm start`）。

---

## 📖 更多文档

- **[Status.md](Status.md)**：完整部署架构参考（三端协同 vs 单机改造方案）、端口与通信清单、核心模块结构、常见坑点排查、以及全版本功能详情。

---

## 🙏 致谢与灵感参考

本项目在设计与实现过程中深度参考并借鉴了以下优秀开源项目的思路：
- [CyberGroupmate](https://github.com/Archeb/CyberGroupmate/) — 群组社交关系模型与注意力语境设计
- [Tulpa](https://github.com/fumingyang2004/Tulpa) — 数字生命伴侣拟真与记忆沉淀架构
- [dsh-bili-scroll](https://github.com/c1overzz/dsh-bili-scroll) — B 站字幕直读、扫码鉴权与自主探索流水线
- [ai-companion-kit](https://github.com/pinganbbOvO/ai-companion-kit) — 拟真陪伴体哲学、未尽心事模型与冒泡切入姿态库
