# GeminiAgyQQAgent — 系统架构、部署参考与演进全景 (`Status.md`)

> **版本基线**: 星见弥 (Hoshimi Mi) V5.4.4  
> **文档定位**: 本文档汇总了本项目的完整系统架构、核心模块职责、**自用部署拓扑参考与单机改造提醒**、关键排错经验及版本演进记录。

---

## ⚠️ 阅读前最重要的说明（关于部署方式）

1. **本项目默认采用“三端分布式协同架构”**（见下文），这是作者为了兼顾 **QQ 固定公网 IP 防风控 + 24h 低功耗静音后台常驻 + 本地显卡配音/桌宠桌面交互** 所搭建的自用环境拓扑。
2. **以下部署方案仅作为架构参考与配置提醒，绝非唯一运行方式！**
3. 如果你只想在 **单台 Windows 电脑、单台 Linux 服务器，或“云端 OneBot + 本地 PC”双机** 上运行，只需要参考下文的「单机/裁剪改造清单」，甚至**直接把本仓库与本 `Status.md` 交给 AI 编程助手**，让 AI 按你的本机环境修改 `config.py`、`.agents/mcp_config.json`、`bot_daemon.py` 与 `DeskPet/config.json` 即可快速跑通。

---

## 🏗️ 一、参考部署架构（三端协同拓扑 vs 单机改造）

### 1. 默认参考拓扑（作者自用三端架构）

```text
                       【外部网络 Internet】
                         │               ▲
           [QQ消息/事件]  │               │ [Gemini / 海外大模型端点]
                         ▼               │
        ┌────────────────────────────────┐
        │ 节点 A：云服务器 (固定公网 IP)    │
        │ · SnowLuma / NapCat (OneBot11) │
        │ · 监听 8300(HTTP) / 8301(WS)   │
        └────────────────┬───────────────┘
                         │ Tailscale 虚拟内网 (或局域网/本机直连)
                         ▼
        ┌───────────────────────────────────────────────┐
        │ 节点 B：常驻边缘大脑主机 (Linux 主机)            │
        │ · bot_daemon.py      常驻守护 + 后台 Workers    │
        │ · bubble_dispatcher  异步气泡 & 桌宠回调 (18302) │
        │ · agy CLI            Antigravity 推理调度       │
        │ · qq_mcp / bili_mcp  MCP 工具服务器 (Stdio)     │
        │ · webui_start.py     管理控制台 (0.0.0.0:8088)  │
        │ · 本地代理 (可选)     如 Mihomo 127.0.0.1:7890   │
        └────────────────┬──────────────────────────────┘
                         │ 局域网 / Tailscale 双向联动
                         ▼
        ┌───────────────────────────────────────────────┐
        │ 节点 C：Windows 桌面与显卡端 (可选)              │
        │ · DeskPet (7797)     Electron 透明桌宠 + CUA    │
        │ · GPT-SoVITS-v5 (9880) 本地 GPU 日语真声合成     │
        └───────────────────────────────────────────────┘
```

### 2. 端口与服务通信速查表

| 服务 / 模块 | 默认端口 | 运行位置 | 作用与通信方向 |
| :--- | :--- | :--- | :--- |
| **SnowLuma / NapCat** | `8300` (HTTP) / `8301` (WS) | 节点 A | OneBot v11 收发消息、表情包/图片 Base64 传输 |
| **Bubble Dispatcher / 桌宠回调** | `18302` (HTTP) | 节点 B (`bot_daemon.py`) | 本机 MCP 发信异步多气泡队列 + 接收 DeskPet 交互/看屏事件 (`/deskpet_event`) |
| **WebUI 管理控制台** | `8088` (HTTP) | 节点 B (`webui_start.py`) | 查看社交拓扑、7D 情绪雷达、记忆库、白名单、频道 `low` 开关、B 站/空间/桌宠面板 |
| **DeskPet 桌宠守护端** | `7797` (HTTP/WS) | 节点 C (`DeskPet/`) | 接收大脑下发的 `pet_say`/`pet_act`/`pet_cua_*` 指令，采集桌面窗口与截图 |
| **GPT-SoVITS-v5 TTS** | `9880` (HTTP) | 节点 C | 为 QQ `send_voice_msg` 与桌宠 `ja_voice` 合成 9 种情感语音 (`wav`) |
| **本地代理 / 节点控制（可选）** | `7890` / `9090` | 节点 B | 仅供 `agy` 访问海外模型端点；国内 QQ CDN、OneBot、B 站均强制直连 |

---

### 3. 自己动手 / 让 AI 帮忙改造成「单机版」的要点清单

如果你想在**单台机器（如个人 Windows 电脑或单台 Linux 主机）**运行，只需改动以下关键位置：

1. **统一网络地址（[config.py](config.py)）**：
   - 将 `BOT_QQ`、`MASTER_QQ`、`NAPCAT_TOKEN` 填为你自己的值（也可用同名环境变量注入）。
   - 单机全跑在本地时，将 `ONEBOT_GATEWAY_IP`、`TTS_PC_TAILSCALE_IP`、`DESKPET_PC_TAILSCALE_IP` 全部设为 `127.0.0.1`（或云+本地双机时仅将 `ONEBOT_GATEWAY_IP` 指向云服务器）。
   - 按实际路径设置 `AGY_EXECUTABLE`，并检查 `get_agy_env()` 中的代理端口（不需要代理或换了端口直接修改 `HTTP_PROXY` / `HTTPS_PROXY`）。
2. **修正 MCP 解释器路径（[.agents/mcp_config.json](.agents/mcp_config.json)）**：
   - 原文件写的是 Linux 路径 `/home/your_user/qqAgent/venv/bin/python`，单机部署时**务必改成你本机的 Python / venv 绝对路径**以及对应的 `qq_mcp_server.py` / `bili_mcp_server.py` 路径，否则 `agy` 将无法调用任何发信和记忆工具。
3. **关闭或替换个人代理脚本（[bot_daemon.py](bot_daemon.py)）**：
   - 根目录下的 [force_global.py](force_global.py) 与 [switch_node.py](switch_node.py) 是针对作者 Clash/Mihomo `127.0.0.1:9090` API 编写的辅助切节点脚本；若你不用这套代理配置，启动前可注释掉 `bot_daemon.py` 里执行 `force_global.py` 的段落（或直接删除该脚本）。
4. **按需精简后台 Worker（[bot_daemon.py](bot_daemon.py)）**：
   - `bot_daemon.py` 启动时会拉起 `breathe_manager`、`daily_memory_summarization_worker`、`daily_qzone_diary_worker`、`qzone_patrol_worker`、`bili_surfing_worker`、`bili_dynamic_daily_worker`、`friend_request_worker`。如果你只想保留纯 QQ 对话或部分生活功能，可直接注释不用的 `asyncio.create_task(...)`。
5. **桌宠客户端适配（[DeskPet/config.json](DeskPet/config.json)）**：
   - 单机 Windows 同时跑大脑和桌宠时，确保 `token` 与 `config.py` 的 `NAPCAT_TOKEN` 一致，`brainCandidates` 包含 `http://127.0.0.1:18302`，`ttsCandidates` 包含 `http://127.0.0.1:9880`。
   - **语音模型说明**：仓库内已排除约 228 MB 的离线识别模型 `DeskPet/models/sensevoice-small-int8`；如果你不额外下载该模型，请保持只用文字气泡/按键交互，或在 `DeskPet/config.json` 中将 `asr.enabled` 设为 `false`。
   - **纯服务端不玩桌宠**：完全无需安装或启动 `DeskPet/`，不影响 QQ 与 WebUI 主体功能。
6. **白名单与推理底座**：
   - 启动前编辑 [allowed_groups.json](allowed_groups.json)（将 `"YOUR_GROUP_ID"` 换成真实群号或留空仅用私聊/WebUI 管理）。
   - 核心大模型调用封装在 [core/agy_runner.py](core/agy_runner.py)（默认通过子进程调用 `agy` CLI 驱动 `gemini-4-hydrogen-{low|medium|high}`）。如果你想接入标准 OpenAI 兼容接口或直连 Gemini HTTP API，只需改造 `core/agy_runner.py` 即可复用上层全部防抖、情绪、记忆与工具链。

---

## 📂 二、核心目录结构地图

```text
.
├── bot_daemon.py               # 常驻主入口：WebSocket 监听、气泡调度器、后台任务装配
├── config.py                   # 全局配置：身份常量、端口、路径、日志分流、代理环境隔离
├── qq_mcp_server.py            # QQ/记忆/音乐/游戏/桌宠 CUA 等 MCP 工具服务端
├── bili_mcp_server.py          # B站账号扫码、搜看、收藏与发动态专属 MCP 服务端
├── webui_start.py              # 8088 WebUI 管理面板启动入口
├── log.py                      # 分流日志实时查看器 (chat / thought / tool / deskpet / error)
├── requirements.txt            # Python 依赖声明
├── .agents/mcp_config.json     # agy CLI 挂载的 MCP Server 路径配置
├── attention/                  # 注意力与语境引擎（防抖队列、社交熵压力、人设、关系分层、屏蔽）
├── core/                       # 运行内核（agy 调用、非阻塞气泡队列、意图路由、状态机、日志分流）
├── social/                     # 社交心智与记忆（7D 情绪 & 437 词库、三层记忆、B站双轨、QQ空间、Libido）
├── tools/                      # MCP 工具具体实现（发信、识图、日语TTS、表情包、空间、桌宠CUA等）
├── toys/                       # 娱乐模块（国际象棋、中国象棋、五子棋、黑白棋引擎 + 高清棋子素材）
├── music/                      # 网易云搜索点歌与歌单服务
├── trends/                     # 晨间热点时事采集缓存
├── workers/                    # 自主生活后台协程（呼吸冒泡、B站冲浪、空间巡检与日记、迎新、内省）
├── webui/                      # FastAPI/原生 HTTP 管理控制台后端与前端面板 (8088)
└── DeskPet/                    # Electron + TypeScript 桌面伴侣（透明穿透、2B爬窗、无钩子屏感/CUA）
```

---

## 🚀 三、核心技术设计与功能亮点

### 1. 并发与防抖对话内核 (`core/`, `attention/`)
- **三车道信号量隔离**：主前台对话 (`GLOBAL_AGY_SEMAPHORE=6`)、B 站后台冲浪 (`BILI_AGY_SEMAPHORE=1`)、事后内省观察者 (`OBSERVER_AGY_SEMAPHORE=1`) 物理隔离，保障聊天响应不被重型后台任务卡死。
- **全通道 `3.0s` 统一防抖 + 开机前热合并**：未调用发信工具前追加新消息可无缝打断重跑；思考/发信期间 (`INFLIGHT_TURNS`) 的普通插话降维沉淀进下轮前情，而群聊直 `@`/唤名/桌宠语音等急件进入 `PENDING_URGENT_TURNS` 补答池；同时支持 **局部通道级 `low` 思考强度开关**（QQ `0-LLM` 指令 + WebUI 双端管理，默认前台走 `medium/high`）。
- **非阻塞多气泡异步投递 (`18302`)**：MCP 发出首条消息并写入异步队列后即刻返回释放进程，后续短气泡按真人打字停顿由 `bot_daemon` 后台 FIFO 投递。
- **长寿命滚动会话 + 防自言自语 + 安全自愈**：群聊 `7m` / 私聊 `10m` 会话 TTL，累计 4–5 轮自动无感换新会话防止 Token 滚雪球；命中云端风控时自动脱敏重推 (`safe_retry_mode`) 并在已有物理发信时绝对抑制兜底话术。

### 2. 七维情绪引擎与三层隔离记忆 (`social/`)
- **E1 物理防通胀 7D 情绪 (`happy / calm / sweet / proud / worry / sulky / jealous`)**：五段高位阻力积分、跨极 `1 : 0.7` 温和共存（自然演绎傲娇口嫌体正直）、两段半衰期指数退潮，配合 `437` 条静态复合情绪词库 (`mood_lexicon.py`) 单行极简注入，并与 GPT-SoVITS-v5 的 9 种参考声线及潜空间融合联动。
- **三层记忆闭环 + 隐私隔离**：L1 凌晨 3 点夜间胶囊总结 / L2 `mid_term_memory.json` 与 `pending_thoughts` / L3 SQLite `chat_archive_fts` 与核心事实；群聊/私聊/QQ空间三通道严格隔离，并支持事后观察者与对话工具三层联动核销已兑现约定。
- **权限白名单 Libido 面板（可选）**：移植 `dsh-libido-panel` 动力学，仅限授权私聊白名单可见与结算，公共群聊和纯净重推模式下 100% 不可见。

### 3. 星见弥桌宠 (`DeskPet/`) 与双轨屏幕感知 / CUA
- **反作弊安全设计**：沿用无钩子方案——`15ms` 鼠标轮询取代全局钩子、`koffi` 直调 Win32 GDI `BitBlt` + `SendInput`（0 进程内存读取 / 0 图形管线注入 / 0 全局驱动钩子），以及内置 `2s` 人类静止让位锁。
- **交互与演出 (`V5.4.4`)**：保留高清透明单图 PNG + 物理弹性形变 + 二次元矢量漫符；支持任务栏悬垂与 **2B 非最大化窗口顶边蹲坐/主动爬窗**；提供装扮页 `hideFromCapture` 截图隐身热切换开关与三端 `proactivePeek` 主动看屏开关。
- **音画分离与日志分流 (`V5.4.2–V5.4.3`)**：气泡展示中文台词，配音走 `ja_voice` 日语真声（识屏/看图 100% 触发，日常触摸/闲聊低概率触发省耗时）；所有桌宠与 `desktop_master` 日志分流至独立 `logs/deskpet.log`（可用 `python log.py deskpet` 单独追踪），保持主控制台洁净。

### 4. B 站自主冲浪、QQ 空间特关与桌游生态 (`workers/`, `toys/`)
- **B 站 3C 双轨与故事板九宫格**：优先原生 `buvid3/4 + bili_ticket + WBI` 协议收藏与发动态，遇风控自动按需唤起无头 Playwright 浏览器兜底（可选依赖）；无字幕视频自动抽取雪碧图拼成 3×3 时间戳九宫格 (`social/bili_storyboard.py`) 交由多模态读片。
- **QQ 空间三档巡检与 2B 策展人日记**：`08:30 / 14:30 / 20:30` 三档随机巡检特关好友（生病/难过说说禁点赞，特关贴心私聊关怀）；每日日记先本地策展 1–2 个真实生活切片再下笔，告别流水账。
- **棋牌小游戏**：内置纯 Python 国际象棋、中国象棋（支持 SAN/中文纵线与坐标双输入，集成高清 PNG 棋子素材）、五子棋、黑白棋及好感度/心情棋风联动。

---

## 🛠️ 四、关键排错与避坑经验 (Troubleshooting)

1. **SnowLuma / NapCat OneBot 关键配置**：
   - Docker 部署 SnowLuma 时必须持久化整个 `/app/data` 目录，否则重启需重新扫码；
   - OneBot 消息格式须选 **`CQ码`**，WebSocket 角色须选 **`Universal`（收发合一）**；
   - 图片发送全量使用 Base64 内存流，不依赖宿主与容器间路径映射。
2. **国内外网络隔离 (`trust_env=False`)**：
   - 访问 OneBot API、腾讯 QQ 图片 CDN 与 B 站 API 时代码中均强制屏蔽系统代理 (`trust_env=False`)；若你看到图片超时或 B 站风控，请优先排查本机代理是否意外劫持了国内直连流量。
3. **日志文件查看方式**：
   - 主运行时会生成分类日志，可通过根目录脚本便捷追踪：`python log.py chat` / `thought` / `tool` / `error` / `deskpet`（均支持实时 `-f` 尾随输出）。
4. **CUA / 桌宠截图看不见弥酱**：
   - 在装扮界面关闭「👻 截图时隐藏弥酱 (`hideFromCapture`)」，对应 [DeskPet/config.json](DeskPet/config.json) 中 `"hideFromCapture": false`，系统会同步取消 Win32 `SetWindowDisplayAffinity` 保护，允许普通截图捕获桌宠自身。

---

## 📜 五、主要版本演进摘要 (V3.0 – V5.4.4)

- **V5.4.4**：回归高清 PNG 立绘物理引擎；新增 Win32 窗口上沿 `ledges` 识别与 2B 主动爬窗/被拎放蹲窗体验；装扮页热开关 `perchWindows` 与 `hideFromCapture`。
- **V5.4.3**：`CategorizedStreamTee` 与工具日志源头分流，DeskPet 全量日志落盘 `logs/deskpet.log` 并支持 `python log.py deskpet`，主滚动控制台零干扰。
- **V5.4.2**：桌宠「中文气泡 + 日语配音 (`ja_voice`)」音画分离与纯中文防误读拦截；识图看屏 100% 配音 + 日常互动低概率配音。
- **V5.4.1**：私聊打断/急件收敛与开机前热合并 (`PRE_SPAWN_PRIVATE_BUFFERS`)；全通道 `3.0s` 统一防抖。
- **V5.4**：集成 `/DeskPet/` Electron 透明桌面伴侣、0-Token 伴随屏幕感知与游戏局内吐槽、双轨 `pet_cua_*` 桌面分析与键鼠操控，打通 `desktop_master` 与主人私聊双向滑窗。
- **V5.3.1**：原生 Python 移植 `dsh-libido-panel` 与 WebUI「🔥 性欲面板」选项卡（默认仅主人私聊白名单生效）。
- **V5.3**：局部通道级 `low` 强度开关；QQ 空间 2B 策展人日记；B 站 3C 混合双轨 + V1 故事板九宫格 + MCP 发动态；增设国际象棋与中国象棋；随机冷场冒泡重构。
- **V5.2 – V5.2.1**：E1 四把物理锁 7D 情绪引擎 + M2 437 条静态词库；前台三档智能强度路由；B 站收藏/动态多段降级与 `B站诊断`；思考期急件补答与 6 项稳定性修复。
- **V4.0 – V5.0.1**：切换 `gemini-4-hydrogen` 模型体系与 `18302` 非阻塞多气泡引擎；GPT-SoVITS-v5 九维多情感潜空间融合语音；长寿命滚动会话、社交熵压力、表情包全生命周期去重与 8088 WebUI 落地。
- **V3.0 – V3.5**：模块化微内核拆分 (`attention/` + `core/` + `social/` + `tools/` + `workers/` + `toys/`)，引入快慢通道隔离、三层记忆体系、B 站自主冲浪与 QQ 空间特关巡检。
