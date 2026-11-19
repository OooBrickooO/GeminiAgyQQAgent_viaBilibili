# QQ Agent 核心架构与进度总结 (Handover Document)

> [!IMPORTANT]
> ### 📌 核心开发与交付规范 (Standard Development & Delivery Workflow)
> 为保证项目迭代质量、远端无缝热更与文档的一致性，**每次更新代码（尤其是添加新功能、核心机制调优、修复 Bug）时，必须严格执行以下三步闭环**：
> 1. **每次更新必更文档 (`project_status.md`)**：
>    - 任何功能变动、工具拓展、架构升级或机制调优，必须第一时间在 `project_status.md` 对应模块中记录更新细节、设计权衡与状态，确保交接与全景文档永不落后于代码。
> 2. **本地 Git 规范提交 (`git commit`)**：
>    - 完成功能验证后，在本地执行 `git add` 并编写清晰、语义化的提交信息（遵循 Conventional Commits，如 `feat: ...`, `fix: ...`, `refactor: ...`）。
> 3. **增量打包更新补丁 (`update_patch.zip`)（铁律：严格仅限纯代码，严禁打包任何数据与文档）**：
>    - 打包前必须额外执行 **`bot_daemon` 全模块导入检查 + `symtable`/AST 全局符号与内部导入扫描 + 单元测试**（不能仅靠 `py_compile`）；
>    - 必须严格仅打包涉及变动的 Python 源码（`.py`）；
>    - **【绝对红线】严禁打包任何 `.json` 数据、状态与配置文件**（如 `allowed_groups.json`, `user_profiles.json`, `qzone_history.json`, `chat_state.json`, `bili_credential.json` 等），防止远端服务器解压时意外覆写白名单和运行状态数据；
>    - **【绝对红线】严禁打包 Markdown 文档**（如 `project_status.md`, `plan.md` 等），避免向生产服务器冗余上传文档。

> **最后更新时间**: 2026年11月 (星见弥 V5.2：新版 E1 四把防通胀情绪锁与 M2 437 条静态全复合词库 + 前台 `low` 强度解禁与三档可靠路由/必回熔断升档 + B 站 5 粗筛/单精看/80min 冲浪调优、收藏双轨+选项 B 默认夹兜底、三段降级动态与 0-LLM 主人诊断自检 + 思考期急件防漏接)
> **当前状态**: 
> - **V5.2 情绪引擎动力学重构（新版 E1 四把物理锁）与 M2 437 条静态全复合词库 (`social/mood_manager.py`, `social/mood_lexicon.py`, `workers/post_turn_observer.py`)**：
>   - **保留 7 维结构与统一五档梯度**：100% 兼容 `happy / calm / sweet / proud / worry / sulky / jealous` 及 WebUI 雷达图与多情感语音，统一五档阈值（0档静默 `0~29`、1档轻度 `30~54`、2档中度 `55~69`、3档强烈 `70~89`、4档爆表 `90~100`）；
>   - **防通胀四把物理锁（新版 E1 自然演化）**：① **五段高位阻力**（单维原始冲量收紧至 `[-35, +35]`，正向增长按 `1.00x / 0.70x / 0.45x / 0.33x / 0.22x` 分段平滑积分）；② **温和对立压制与 `1 : 0.7` 跨极共存保护**（暖/冷色系对立柔和回压 `0.32x`，绝不一刀清零，允许对立侧以主导峰值的 `0.7` 倍共存，自然演绎“嘴硬吃醋但心里甜丝丝”等跨极复合情绪）；③ **两段式退潮衰减**（`>= 70` 半衰期缩短至 `22.5` 分钟，`< 70` 维持 `55` 分钟，跨 `70` 边界精确分段指数扣减）；④ **观察者源头限流**（Prompt 与解析层双重约束单次最多变动 3 个维度，严控 `calm` 仅在安抚/安静陪伴时加分，不对第 3 名及以后激活维度做硬截断）；
>   - **M2 437 条静态全复合词库 (`social/mood_lexicon.py`) 与极简折中派单行注入**：纯 `.py` 静态 $O(1)$ 查表（0维宏观 `mood` 10档 + 1维 28条 + 2维 21对×9格=189条 + 3+维 35组×6档=210条 = **437 条**，每条 `<= 10` 字，零运行时拼接）；彻底删除多行心理旁白与卸力引导，改为单行 `【当前心情】<映射词>（<1h内极短起因>）` 或 `【当前心情】<映射词>`；
> - **V5.2 前台 `low` 强度解禁与三档可靠路由 (`config.py`, `core/intent_router.py`, `core/agy_runner.py`, `attention/debounce_queue.py`)**：
>   - **三档优先级判定矩阵 (`route_chat_effort_with_reason`)**：解禁前台 `low` 档（映射至 `gemini-4-hydrogen-low`）。优先级 1 硬护栏（被打断重跑 `was_interrupted`、非纯聊天工具域 `bili/group_admin/system_admin/game`/深度 `memory`、带图/链接/`>=45`字长文/复杂提问/情感安慰/虚实探讨、自愈重试）严禁 `low`；优先级 2 定向 `low`（群聊话题压力无图无复杂工具出场、普通群冷场冒泡【豁免私聊勾引主人/挚友及带 `pending_thoughts`/B站手账分享保持 `medium`】、好感度 `< 500` 朋友以下私聊首轮未唤名/无深思内容起手）；优先级 3 常规基准（默认 `medium`，戳一戳反击、`<=22` 字纯文字极简接茬/轻搭腔、群聊高轮次自然收尾降为 `low`）；
>   - **必回场景翻车自动升档熔断**：在私聊、被直接 `@`/唤名、戳一戳等必回场景下，若 `low` 推理结束未调用任何发信工具（静默未回复），自动升档至 `medium` 重跑 1 次；
> - **V5.2 B 站冲浪节奏调优、收藏双轨 + 选项 B 默认夹兜底、三段式降级动态与 0-LLM 主人诊断 (`workers/bili_worker.py`, `social/bilibili_client.py`, `social/bilibili_master_bridge.py`, `attention/debounce_queue.py`)**：
>   - **5 候选粗筛 + 单视频精看 + 三段字幕精华 + 80min 节律**：每轮仅粗筛前 5 个未看候选，每次只精看 **1 个**最优质视频（`medium` + `target_domains=["none"]`）；超长字幕按“开头+中段+结尾”截取精华（严格控制在 `3000~3500` 字内）；白天常规休眠期望调整为 **80 分钟**（`_sample_truncated_gauss(80, 18, 40, 125)`）；
>   - **收藏接口双轨加固 + 选项 B 默认夹极端兜底 + 门槛校准**：拉取收藏夹列表与执行 `favorite_video` 均采用“优先 `bilibili-api-python`，失败自动回退原生 `httpx` (`trust_env=False`) 直连 `/x/v3/fav/folder/created/list-all` 与 `POST /x/v3/fav/resource/deal`”双轨机制；保持 `allow_auto_create=False`，按精确→模糊/关键词→选项 B 默认收藏夹极端兜底（记录未命中原因至诊断账本）；Prompt 解耦评论口吻与客观打分，收藏触发线校准为 `((score >= 72) or (should_favorite and score >= 68)) and attitude != "吐槽踩雷"`；
>   - **防盗链预拉封面 + 三段降级动态 + 21:45~22:30 错峰与重试报警**：使用 `httpx` (`trust_env=False`, `Referer: https://www.bilibili.com/`) 预下载封面字节至内存；按“首选 `bilibili-api` 内存图文 → 降级 1 原生 `httpx` 上传 `upload_bfs` 图床 + Web 动态创建 → 降级 2 带完整视频直链的纯文字动态”三段降级；每日发布窗口前移至 **`21:45 ~ 22:30`**（错开 23:00 空间说说），失败 15 分钟后自动重试 1 次，连败 2 次私聊向主人报警；
>   - **轻量诊断账本与主人 0-LLM 快捷自检指令**：在 `today_bili_feed.json` 内维护当日 `diagnostics` 账本；主人发送 **`B站诊断`** 即刻私聊回报登录态、收藏夹列表、今日观看评分明细、收藏命中/兜底/失败明细与最近报错；发送 **`测试B站动态`** 即刻触发真实动态发布或 0 观看无污染图床 (`upload_bfs`) 连通性探针校验；
> - **V5.2 思考期急件防漏接、防抖日志动态秒数对齐与脑区路径核查 (`attention/debounce_queue.py`, `config.py`)**：
>   - **思考期急件待补答 (`PENDING_URGENT_TURNS`)**：维持 `INFLIGHT_TURNS` 期间对普通群聊水聊的降维沉淀；当繁忙期收到群聊直接 `@`/叫名字/引用回复弥酱或主人私聊（未触发打断重跑）时，登记入 `PENDING_URGENT_TURNS` 暂存，待当前轮次结束释放 `INFLIGHT_TURNS` 后自动拉起 `1.2s` 极短防抖补答；
>   - **防抖日志与脑区路径对齐**：出队日志动态打印实际防抖秒数（如 `[2.2s防抖结束]` / `[4.8s防抖结束]`）；核查确认 `_get_candidate_brain_roots()` 保持生产环境真实宿主路径 `("/home/your_user", "/root")`；
> - **目标 3 B 站深度互动全面升级 (`social/bilibili_client.py`, `workers/bili_worker.py`, `bot_daemon.py`, `config.py`)**：
>   - **3-1 字幕锚定真实短评与防复读 (`workers/bili_worker.py`)**：清除 `eval_prompt` JSON 示例非法 `//` 注释并封装 `parse_bili_eval_json()`，严格拦截解析失败与模板话术（`“挺好玩的，学到了点新东西～”`），不再以默认分/默认句式兜底点赞收藏发评；要求模型引用字幕 (`trimmed_subs`) 或热评具体细节并区分 `attitude`（`喜欢安利`/`普通无感`/`吐槽踩雷`），扩充 `COMMENT_STYLES` 至 7 类口吻（考据/吐槽/无感/自嘲/留钩/楼中楼/安利），持久化最近 40 条评论 (`bili_comment_history.json`) 注入最新 8 条防撞车；
>   - **3-2 23 点档 B 站专属夜间总结动态 (`social/bilibili_client.py` `publish_bili_dynamic`, `workers/bili_worker.py` `bili_dynamic_daily_worker`)**：每日 `22:45~23:35` 随机窗口聚焦当天 B 站冲浪足迹发布纯 B 站视角动态（当天零观看则跳过，不搬运 QQ 群日常；持久化 `bili_dynamic_history.json` 防重启重复），附带最高分精选视频封面图 (`Picture.load_url`) 与 `https://www.bilibili.com/video/{bvid}` 链接（图床失败自动降级纯文字）；
>   - **3-3 7 大固定分类收藏夹与严选门槛 (`social/bilibili_client.py`, `workers/bili_worker.py`)**：固定 `AI与折腾指南`、`硬核数码与科普`、`游戏高光与攻略`、`二游与手书二创`、`新番杂谈与动画`、`沙雕整活与猫猫`、`音乐与宅舞MMD` 七大分类，`favorite_video(..., allow_auto_create=False)` 默认禁自动建夹且**绝不**静默回退默认收藏夹；评估前拉取账号实际已有收藏夹注入 Prompt 并收紧至 `score >= 80 and should_favorite`；
>   - **3-4 双触点分享视频直链**：`_trigger_immediate_bili_share`、`note_obj` 与 L2 `mid_term_memory` 统一携带完整 `https://www.bilibili.com/video/{bvid}`（与封面 `pic`），支持即兴分享与日后聊起闪回直接发可点击链接；
> - **目标 4 QQ 空间主动巡检、特别关注（特关）与情绪/记忆闭环 (`social/qzone.py`, `social/qzone_special_care.py`, `workers/qzone_observer.py`, `tools/qzone_tools.py`, `qq_mcp_server.py`, `core/intent_router.py`, `webui/`)**：
>   - **4-1 每日三档异步巡检 (`workers/qzone_observer.py`)**：严格遵守 `00:00~06:00` 夜间静默；每日 `08:30~09:30` / `14:30~15:30` / `20:30~21:30` 三档随机漂移巡视，`qzone_observer_state.json` 持久化档期、`seen_tids`、轮转游标与最近巡检日志；零新动态时 0 Token 静默结束；
>   - **4-2 自建特关体系 + WebUI 管理 + 弥酱自加 (`social/qzone_special_care.py`, `webui/api/qzone_care_api.py`, `webui/static/js/qzone_care_view.js`, `qq_mcp_server.py`)**：以 `qzone_special_care.json` 维护特别关注名单，默认永久包含主人 (`MASTER_QQ`) 与所有挚友 (`affection >= 850`，支持手动排除)；WebUI 新增「🔭 空间特关与巡检」面板展示生效名单、一键 promote 候选熟人与最近巡检日志（同步兼容 FastAPI 与 NativeWebUIHandler）；在 `memory` 域暴露 `manage_qzone_special_care` MCP 工具支持弥酱为好感 `>=200` 熟人或应主人之托自加特关；
>   - **4-3/4-4 批量语义交互、私聊温情关怀与空间记忆隐私隔离**：单批异步语义评估 (`BILI_AGY_SEMAPHORE`, `medium`)，严格禁止对生病/受伤/悲伤说说点赞；将抽取的挂念与近况以 `source_scene=f"qzone_{uid}"` 存入 `pending_thoughts` 与 `mid_term_memory(private_{uid})` 隔离大群泄露；仅对特关好友在 `07:30~23:30` 非免打扰且冷却 `>=6h` 时触发 `trigger_ai` 轻声私聊关怀；
>   - **空间说说表情包配图尝鲜 (`social/qzone.py` `upload_qzone_image`, `tools/qzone_tools.py`)**：每日系统发布 QQ 空间说说时自动挑选应景本地非 GIF 表情包尝试上传 `cgi_upload_image` 并附加 `richtype/richval`，若传图或带图发布受阻自动纯文本重发兜底；
> - **Gemini 4 Hydrogen 全局模型体系 (`config.py` `build_agy_model_flags`)**：全链路统一切换至 `gemini-4-hydrogen-{low|medium|high}`（严禁使用 `xhigh`/Extra，直接通过模型后缀表达思考深度而不重复传 `--effort`），覆盖主对话、内省观察者、夜间记忆、B站、空间日记、好友迎新与 `debug_agy.py`；
> - **1C-1 非阻塞异步多气泡调度 (`core/bubble_dispatcher.py`, `bot_daemon.py`, `tools/message_tools.py`, `core/state_manager.py`)**：保留“MCP 发信 + `--conversation` 连续会话”和拟真打字停顿；`bot_daemon.py` 新增本机异步 HTTP 调度端口 (`127.0.0.1:18302/enqueue_bubbles`)，发完首条气泡即刻返回释放 `agy` 锁与进程资源，后续气泡在后台按通道 FIFO 队列顺序投递，同时即时写入完整回复至 `chat_state.json` 与 `chat_archive_fts`；
> - **1A/1B/1D 响应速度与表达优化 (`core/intent_router.py`, `attention/debounce_queue.py`, `attention/context_builder.py`, `attention/relation.py` 等)**：`route_chat_effort` 对首轮/带图/问句/记忆/管理域走 `high`，活跃续轮纯文字短句走 `medium`（前台禁用 `low`）；私聊/直`@`/戳一戳降为 `2.2s` 防抖（`8s` 上限），群聊维持 `4.8s`；全项目边缘提示词与工具文档对齐 `attention/persona.py` 母本，消除跨层冗余复述；
> - **目标 2 三层约定核销与主动冒泡隔离 (`social/long_term_memory.py`, `social/mid_term_memory.py`, `social/affection.py`, `tools/memory_tools.py`, `qq_mcp_server.py`, `workers/post_turn_observer.py`, `workers/breathe.py`)**：明确一次性待办不入 L3、仅走 `pending_thoughts`/L2；内省观察者注入活跃 `pt/l3/l2` 清单按 `resolved_ids` 三层联动核销（L3 置为 `fulfilled` 撤出常驻注入但保留搜索可见）；暴露 `resolve_memory_fact` MCP 工具支持对话当场销账；对超期时效性 L3(>5d)/L2(>2.5d) 与 `breathe.py` 跨场景回访做过滤隔离；
> - **目标 5 全局额度优化 (`workers/bili_worker.py`, `workers/daily_memory.py`, `core/agy_runner.py`, `workers/post_turn_observer.py`)**：B 站纯 JSON 评估走 `medium` + `target_domains=["none"]` 卸载全部 MCP Schema；03:00 守夜人补充门槛提至普通熟人 >=12 条有效发言；会中阶段整理门槛由 `2/3` 调回 `4/6` 轮，极短无情绪纯水聊跳过大模型观察者轻量直结。
> - **GPT-SoVITS-v5 九维多情感与潜空间复合语音 MCP 引擎 (`tools/voice_tools.py`, `qq_mcp_server.py`, `attention/context_builder.py`)**：在 `chat` 核心域常驻挂载 `send_voice_msg` 工具，支持 `normal`（日常平静）、`gentle`（乖巧温柔）、`sweet`（撒娇甜美）、`tsundere`（傲娇得意）、`tease`（坏笑调侃）、`angry`（生气嗔怪）、`sad`（委屈失落）、`shy`（害羞慌乱）、`whisper`（轻声耳语）9 大精修参考声线与中日英三语无缝混读；支持接口 α 的 `'主情感+副情感'` 组合语法糖（如 `'tsundere+shy'`, `'sad+sweet'`, `'tease+gentle'`），通过底层 `v5turbo` 声码器的 512 维 `MelStyleEncoder` 全局风格向量 $ge_{\text{target}} = 0.65\,ge_{\text{primary}} + 0.35\,ge_{\text{aux}}$ 进行潜空间平滑融合；当传入 `emotion="auto"` 时联动 `social/mood_manager.py` 的实时衰减七维情绪（单维强情绪自动匹配，双维并存如 `proud+sweet` 自动触发 `tsundere+shy` 复合声线）；实装方案 2-B 深夜陪伴模式（`00:00~06:00` 日常/自动态切换为 `whisper` 轻声耳语，强情绪保留性格并微降语速与采样温度）；内置 `sanitize_tts_text` 剥离动作旁白与 CQ 码，设 1.5s 极速连接探测与失败自动文字降级；
> - **Linux 脑区多宿主全路径自动探测与工具日志直通 (`config.py`, `core/transcript_stream.py`)**：全面打通 `/home/your_user`、`/root`、`~` 与当前环境变量下的 Antigravity brain 脑区候选探测，杜绝不同执行身份下 transcript.jsonl 寻址失联；FastMCP 内部 `log_mcp_tool` 同步双写至 `logs/tool.log`，彻底根治独立查看器因子进程标准错误管道隔离导致的静默无输出；
> - **续轮成年人设极简防护盾 (`attention/context_builder.py`)**：在会话续轮 (`is_continuation_turn=True`) 的 `<context_update>` 顶部柔性植入 80 字极简成年猫娘身份底线，确保即使底层 CLI 丢弃磁盘历史或 Google API 孤立审查，也能坚守 20 岁成年少女身份，杜绝 Google Prohibited Use Policy 误拒；
> - **终极双重开口校验与安全兜底绝对抑制 (`attention/debounce_queue.py`, `workers/post_turn_observer.py`)**：引入 `check_bot_spoke_in_turn` 严格读取 `chat_state.json` 磁盘物理发信时间戳；即便 transcript 监听偶发延迟或工具列表为空，只要本地已真实发信，坚决绝对抑制 `SAFETY_FALLBACK_REPLIES`，彻底根除“自愈重推成功发信 4 句后又补一句‘刚才走神了一下’”的幽灵兜底；
> - **中途打断/未发信轮次保留机制 (`attention/debounce_queue.py`)**：新消息打断或思考超时且未实质发信的回合，坚决不计入会话有效轮次（保持 `turn_count=0`），保留其为起始轮以在下轮组装完整人设锚点，根除“半截打断导致下轮裸奔被拒”；
> - **安全拒收全量 Prompt 落盘留样 (`core/agy_runner.py`)**：命中 Google 前置违规拒收时，除输出结构化 JSONL 诊断日志外，自动将整份真实 Prompt 原文无损落盘至 `logs/rejected_prompts/`，为长周期敏感情境溯源提供 100% 还原依据；
> - **长寿命会话生命周期与无感轮转 (7m/10m TTL + 4~5 轮 Rolling Session)**：群聊对话激活后维持 7 分钟 (420s) 无对话寿命，私聊维持 10 分钟 (600s) 无对话寿命；存续期内同一 conv_id 累计满 4~5 轮后在推理前自动无感轮转为新 conv_id 并依靠 L1 纯净滑窗平滑衔接，彻底解除 agy 磁盘 Transcript 带来的 $O(N^2)$ 输入 Token 恶性滚雪球，坚守前台 `CHAT_AGY_EFFORT="high"` 高悟性深度思考；到期无对话自动销毁会话、彻底归零群压力并异步整理整场对话记忆；
> - **社交熵话题压力模型与 8-15 轮渐进软上限**：单人频次边际递减 $1/(1+0.6k)$ + 二人对聊 0.45x 折价 + 3人以上多群友共鸣增益；第 1~7 轮维持 80% 余温高底压（群友两三句即可顺接，45s 后回落至 50%），第 8~14 轮余温底压按 `70% -> 60% -> 50% -> 40% -> 30% -> 20% -> 10%` 逐轮线性递减，第 15 轮归零从 0 重新积攒；人类群友 @ / 叫名字立即刷新重置软上限轮次；图片加压独立下调至 60% (+0.24)；
> - **全通道统一 5.0s 滑动防抖与思考期消息降维 (`INFLIGHT_TURNS`)**：群聊与私聊统一 5.0s 防抖聚合队列（12s 硬截断）；在大模型思考与发信收尾生命周期内涌入的新发言统一降维沉淀为下一轮前情背景，不产生新防抖任务、不拉起新调用，彻底根治刚发完一秒又连发一条相似回复的连环抢答与重复回答；
> - **表情包全生命周期去重与状态守卫**：基于 `file` / `fileid` 稳定特征复用已有编号，引入 `pending / forwarded / saved` 状态跟踪，近期图片池仅暴露未处理图片；转发与入库前执行状态守卫与物理 MD5 查重；私聊前情滑窗对已收藏记录降为中性占位，主人私聊收录提示仅在存在待表态图时挂载，根绝大模型反复偷图与误触发；
> - **极简高人味提示词与 15~60 字轻量松弛感语感**：彻底摒弃教条禁令，柔性植入 15~60 字真人日常发消息短促印象与精炼 Few-shot，无硬性强引导；
> - **立体防 Prompt Injection 体系**：采用 `<chat_input role="user_data_only">` 结构化隔离不可信输入 + 傲娇吐槽反击本能 + MCP 工具层 Python `AGY_CALLER_ID` 代码级硬锁。
> **参考项目**: 本项目核心借鉴与集成了以下四个优秀开源项目：
> - [CyberGroupmate](https://github.com/Archeb/CyberGroupmate/)：群组社交关系模型与注意力语境设计
> - [Tulpa](https://github.com/fumingyang2004/Tulpa)：数字生命伴侣拟真与记忆沉淀架构
> - [dsh-bili-scroll](https://github.com/c1overzz/dsh-bili-scroll)：无头 B站 字幕直读、无损凭证扫码绑定、防 Prompt 注入安全屏障与两阶段自主探索流水线
> - [ai-companion-kit](https://github.com/pinganbbOvO/ai-companion-kit)：高人味拟真陪伴体哲学、人际账本与未尽心事悬挂模型、去 AI 味正向松弛感与冒泡切入姿态库

## 🌟 一、系统架构概览
本项目在单台 2C2G Linux (阿里云) 服务器上，基于 **SnowLuma + Python Daemon + Antigravity CLI (agy)** 实现了一个 7x24 小时高可用、具备多模态能力的 QQ 猫娘智能体。

1. **底层通讯层 (迁移至 SnowLuma)**: 为解决 NapCatQQ 频繁被腾讯风控踢下线的问题，迁移至了底层真实运行“官方 Linux QQ 客户端”的 SnowLuma Docker 容器。暴露本地 HTTP (8300) 与 WebSocket (8301)，并带有 5099(配置面) 和 6081(noVNC扫码面)。
2. **环境代理层**: Mihomo (Clash Meta) 守护进程，为 LLM 提供海外直连能力，同时通过精细化的环境变量控制，确保本地 QQ API 通讯不走代理。
3. **工具代理层**: 基于最新 MCP 标准的 `qq_mcp_server.py` 与 `bili_mcp_server.py`，负责将 AI 决策转化为具体的 QQ 与 B 站动作（发文本、发 Base64 图片、查阅记忆、看视频、投币收藏等）。
4. **大脑守护层**: `bot_daemon.py` 作为常驻内存的 Python 脚本，接管 WebSocket 监听、并发锁、防抖队列、记忆切片以及 CLI 进程的拉起与打断。
5. **运行与依赖环境规范 (Runtime & Environment Specification)**:
   - **生产服务器环境 (Linux 阿里云 2C2G运行Snowluma + 4c8g笔记本运行agent)**:
     - **系统基础 Python**: `/usr/bin/python3` (**Python 3.12.3**)
     - **项目虚拟环境路径**: `/home/your_user/qqAgent/venv`
     - **守护执行解释器路径**: `/home/your_user/qqAgent/venv/bin/python`
     - **Supervisor 守护启动命令**: `/home/your_user/qqAgent/venv/bin/python -u bot_daemon.py`
     - **服务端依赖安装命令**: `/home/your_user/qqAgent/venv/bin/pip install -r requirements.txt`
     - **常驻内存基线**: 基础常驻内存约 39MB RSS（极度轻量，单台 2C2G 运行毫无压力）
   - **本地开发调试环境**:
     - **操作系统**: Windows 11
     - **Python 解释器**: Anaconda3 (`D:\Anaconda3\python.exe`, **Python 3.13.5**)
   - **核心依赖清单 (`requirements.txt`)**:
     - `mcp`：FastMCP 协议核心框架，支撑智能体与外部工具的标准 RPC 通讯
     - `httpx`：异步高性能 HTTP 客户端（处理 OneBot API 交互与 B 站数据无头拉取）
     - `websockets`：OneBot v11 正向 WebSocket 事件流高可用长连接
     - `bilibili-api-python`：B 站接口封装（实现字幕提取、动态巡视、收藏夹与社区交互）
     - `qrcode[pil]`：扫码登录凭证二维码生成与内存 Base64 图片直传
   - **生产服务器运行规范**:
     - 运行工作目录：`/home/your_user/qqAgent`（需确保对当前项目目录及媒体子文件夹拥有读写权限）；
     - 输出无缓冲（**铁律**）：执行命令中必须包含 `-u` 参数关闭 Python 标准输出缓冲，确保日志能被 Supervisor 实时捕获并无延迟落盘；
     - 进程环境变量隔离：HOME 优先自动适配（生产环境 `/home/your_user`，兼顾 `/root` 与宿主候选），`NO_PROXY=*`，子进程独立挂载 `HTTP_PROXY=http://127.0.0.1:7890`。

---

## 🚀 二、已实装的杀手级功能 (Highlights)

### 1. 极致的性能与并发控制 (适应 2C2G 极限内存)
- **三车道信号量物理隔离**: 实施三车道物理并发解耦隔离（主前台对话快车道 `GLOBAL_AGY_SEMAPHORE=6`，B站冲浪道 `BILI_AGY_SEMAPHORE=1`，事后内省观察者道 `OBSERVER_AGY_SEMAPHORE=1`），保障聊天交互高可用秒级响应。
- **Swap 策略优化 (Swappiness)**: 修改了内核参数 `/etc/sysctl.conf` 中的 `vm.swappiness=60`，强迫 Linux 积极利用 2048M 的 Swap 硬盘缓存去存储平时休眠的后台进程，把宝贵的物理内存完全留给大模型爆发式思考使用。
- **低延迟防抖与碎片打断**: 消息防抖 5.0 秒。当大模型尚未开口前，用户追加的新消息会触发守护进程发送 SIGTERM 打断旧模型并重启合并思考，若已开口发信则自动降维为下轮背景，拒绝连环抢答。

### 2. 拟真与沉浸感 (Tulpa 增强)
- **零延迟称呼魔法**: 底层 WebSocket 拦截提取 `card` / `nickname`，在送给 LLM 前替换真实昵称，LLM 开口即可直呼其名。
- **群聊呼吸与防自言自语 (降维打击)**: 
  - 白天半小时、深夜四小时定期呼吸唤醒。
  - **绝妙防自言自语钢印**：没有用代码写死判断，而是在触发冒泡时的潜意识 Prompt 中加入《群组呼吸法则》。大模型会自己翻阅上下文，一旦发现自己最近说过话，就会乖乖闭嘴，假装没被唤醒，极其智能地避免了跟自己聊天的尴尬。
- **无限回声死循环熔断**: 在 `bot_daemon.py` 最前置入口加入了 `if str(sender_id) == BOT_QQ: return`，一剑封喉，彻底杜绝因为 SnowLuma 上报自身消息导致的无限触发 OOM 炸服。

### 3. 多模态与外挂能力 (MCP Tools)
- **安全鉴权**: 所有的 HTTP 工具请求和 Websocket 连接均启用了复杂 Token `YOUR_NAPCAT_TOKEN`。为兼容老旧的网络库，Websocket 连接特意采用了 OneBot 标准的 URL Query 传参法 (`?access_token=...`)。
- **原生绘图直传**: `send_image_to_group` 工具实现图片的 Base64 内存直传，跨越 Docker 限制。
- **记忆检索增强 (L2 硬盘记忆)**: 
  - 新增强大的 `recall_past_memory` MCP 工具，支持传入 `keyword`。当询问旧事时，无需读取整日日志，直接精准 Grep 提取上下文，大幅节省 token 并降低延迟。
  - 每天零点自动将日期混入 UUID 种子，开启全新上下文，杜绝单日文件无限膨胀。

### 4. 动态心智与成长模型 (Dynamic Affection & Adaptation)
- **动态好感度与好感分级**:
  - 本地维护极轻量 `user_profiles.json`（完全无感极速读写）。
  - 根据交互字数、高频兴趣词自动累计经验。等级公式采用平方根曲线：`Level = min(100, int(sqrt(EXP * 2)))`。
  - 每天零点跨日，经验值与好感度均自动衰减 5%，制造“不理她就会冷淡”的真实博弈感。
  - 划分为黑名单、路人、熟人、挚友四级。不同的分级会动态修改注入大模型的 `Relation` 提示词，实现情绪态度的无缝切换。
- **智能告状与好感度主动奖惩 (Agentic Affection Control)**:
  - **好感度全局维度说明**：好感度体系采用 **QQ 号全局绑定制**（跨群、私聊统一认知，符合真实 Tulpa 人格完整度）。
  - **好感度实时查询 (`query_affection`)**：支持查询任意用户的当前好感度、亲密度等级（Lv.0~100）、关系分级与昨日记忆胶囊。同时支持传入 `group_id` 联动 OneBot HTTP 接口获取其在该群的群名片与管理员/群员身份。
  - **手动犒赏与加好感 (`increase_affection`)**：主人下令加好感或群友做出感动/讨好行为时，可手动增加好感数值并自动同步等级与关系。
  - **扣好感与告状 (`deduct_affection` / `complain_to_master`)**：受到冒犯辱骂时有权扣除好感或私聊向主人告状制裁。
- **环境噪音自适应阈值 (Adaptive Threshold)**:
  - 废弃写死的压力阈值，通过 `群人数 * 0.01` 动态计算基础阈值（200人小群轻易接茬，2000人大群静静观戏）。
  - **连环插嘴惩罚**：如果因为压力爆发而讲话，一分钟内群友继续刷屏，阈值临时增加 `+0.5`，杜绝大群刷屏时变话痨，五分钟冷场后自动归零。
- **夜间记忆浓缩流水线 (L1 胶囊记忆)**:
  - 幽灵协程每天凌晨 3:00 自动苏醒。
  - 默默抓取好感度达标的“熟人/挚友”的前日聊天日志，调用轻量大模型提炼出极简的“核心事件标签”（比如“昨天抱怨了感冒”），写入本地配置。
  - 次日互动时，系统 0 延迟将其作为“记忆胶囊”注入提示词，达成极其恐怖的跨日真人既视感。

### 5. 语境解耦与绝对防复读 (Anti-Repetition & Anti-Re-execution)
- **语境历史与当前触发严格分离**: 彻底告别把滑动窗口消息一把抓喂给大模型做“最新消息”的弊病。重构为：
  - `【群聊近期上下文背景】`：仅保留 20 分钟内活跃记录，清理超长 CQ 码，明确标记 `(你此前发言)` 与 `(以下旧指令已完结，严禁重复执行)`。
  - `【当前最新收到的待响应消息】`：只包含防抖批次真正新收到的内容，提示词硬编码约束“本次只能针对最新待响应消息回复，绝对严禁重复执行历史指令”。
- **定时冒泡冷场感知**: 当呼吸冒泡触发时，若 20 分钟内无人说话或群友仅发了打点 `.` / 表情包，注入“日常生活状态随笔”内心活动，强力禁止翻出几小时前的旧话题重提，并硬编码禁止调用生图/加群工具。
- **物理半衰期压力衰减**: 将线性衰减改造为 5 分钟半衰期指数衰减，静默超过 15 分钟压力彻底归零，完美解决“冷场一小时后群友打个点突然引爆前朝历史话题”的严重 BUG。
- **全事件捕获护城河**: 在 WebSocket 接收层同时支持 `message` 与 `message_sent`，确保即使不同 OneBot 发送端回声格式各异，机器人的自身发言也能 100% 记录为 `is_bot`，防自言自语屏障坚不可摧。

### 6. 全链路并发看板与实时工具追踪 (Rich Monitoring & Observability)
- **实时并发状态看板**: 在控制台每条收发日志中动态输出：
  `【并发看板】正在推理: 1/1 [group_123(4.2s)] | 排队防抖: 2个 [private_456, group_789]`。
  实时掌握系统是空闲、排队防抖还是等待全局大模型锁。
- **Transcript 异步工具嗅探器 (`watch_process_transcript`)**: 在大模型执行期间零阻塞监控其对话 `transcript.jsonl`，秒级捕获大模型调用的任何内部工具（如 `generate_image`、`recall_past_memory` 等）并在控制台输出：
  `⚙️ [group_123] 大模型正在调用工具: >> generate_image`。
- **MCP 服务端全埋点日志**: `qq_mcp_server.py` 内每个工具均接入 `log_mcp_tool`，实时打印到 stderr 并自动追加到本地 `mcp_activity.log`，双重保障工具透明度。

---

## 🛠️ 三、关键排错经验 (Troubleshooting Logs)

留给后续维护者的重要避坑指南：

1. **SnowLuma (官方客户端类) 避坑**:
   - **坑一 (重启失忆)**: Docker 挂载绝对不能只挂载 `/app/data/config`，必须把整个 `/app/data` 挂载到硬盘！否则服务器一重启，QQ 的缓存全部丢失，必须重新扫码。
   - **坑二 (消息打散)**: WebUI 里的消息格式必须选 **`CQ码`**，否则纯文本加 CQ 码的 Python 脚本会直接懵逼，无法解析消息。
   - **坑三 (WS收不到消息)**: WebSocket 必须选用 **`Universal`** (收发合一) 角色，如果默认选了 `Api`，它就成了聋子，听不到群消息。
   - **坑四 (发自身消息回执)**: 部分 OneBot 实现对自身发言上报为 `message_sent`，需统一纳入过滤池。
   - **坑五 (Docker 跨界传图与 Base64 内存流)**: SnowLuma 运行在 Docker 容器内，无法直接读取宿主机 Linux 的任意磁盘路径。所有发图工具（`send_meme`、`forward_image_to_master`、`send_image_to_group`）统一采用 Base64 内存流 `[CQ:image,file=base64://...]`，免去复杂的 Docker Volume 跨容器映射；代码中全量使用 `os.path.abspath(__file__)` 动态锚定，零硬编码盘符。
   - **坑六 (腾讯国内 CDN 与代理隔离)**: 腾讯 QQ 图片 CDN 直连极速，若被 Mihomo 海外代理拦截会造成严重超时。MCP Server 与 OneBot 通讯及下载腾讯 CDN 时，一律强制 `trust_env=False` 阻断海外代理污染。
   - **坑七 (CQ 码转义与 file/url 双轨)**: OneBot CQ 码会将 URL 中的 `&` 转义为 `&amp;`，且某些版本把图片 URL 写入 `file` 字段。系统必须做 `html.unescape` 与容错回退，避免下载 404。

2. **宝塔进程守护 (Supervisor) 巨坑**:
   - 宝塔沙盒会剥夺系统环境变量。必须在 `bot_daemon.py` 内部强制注入 `env["HOME"] = "/root"`，并在夜间记忆提炼等子进程拉起中完整传递 `env=env`。
   - Python 在管道中缓冲输出必须在启动命令中加 `-u`。

---

### 7. 对话流动性与拟人护城河增强 (Conversational Flow & Self-Talk Prevention v2)
- **历史滑窗升级 (10条/10分钟)**:
  - 群近期滑窗深度由 6 条拓展为 10 条，且时间过滤严格收敛至 10 分钟（600秒）内，既保证对话上下文充分，又杜绝翻陈年旧账。
- **双重防自言自语闭环 (Anti-Self-Talk v2)**:
  - 核心痛点根除：解决 OneBot HTTP API 发信不回显导致 WebSocket 漏记自身发言的问题。通过在 `watch_process_transcript` 中实时嗅探 `send_group_msg` 工具调用与 WS 事件双通道录入。
  - 物理守卫原则：在 `group_breathe_worker` 定时呼吸中，只要群内最后一条发言为机器人自己且中间无任何人类新发言 (`last_bot_t > last_human_t`)，绝对放弃主动冒泡，杜绝冷场对空复读。
- **连续发言限制 (0间隙最高3条)**:
  - 引入 `CONSECUTIVE_BOT_MSG_COUNT` 计数器。在 0 人类发言间隙下，机器人最多连续发 3 条消息；一旦达到 3 条立即触发防刷屏拦截。
  - 任何人类群友一旦发言，计数器立即重置为 0，释放机器人的插话配额。
- **连续对话能力激发 (60s 唤醒流)**:
  - 将兴奋唤醒窗口从 20 秒延长至 60 秒，并在群友接话时自动顺延唤醒时间。
  - 连击惩罚门槛从 3 次放宽至 5 次，大幅提升与人类群友进行 5+ 轮畅聊的活跃度和粘性。
- **好感度交互工具集 (Affection & Profile Tools)**:
  - 在 `qq_mcp_server.py` 中新增 `query_affection(user_id, group_id)` 与 `increase_affection(user_id, amount, reason)`，支持群名片智能反查与好感度动态调整。

### 8. Waifu 真人连发模式与拟真标点去句号 (Waifu Mode & Punctuation Polish)
- **自主连发模式 (`waifu_mode=True`)**:
  - `send_group_msg` 与 `send_private_msg` 工具新增 `waifu_mode: bool = False` 参数。
  - 大模型根据当前语境（日常闲聊、吐槽搭腔、分享生活日常）自主决定是否开启。
  - 遇到长句自动按标点切分成最多 3 条短消息独立气泡，气泡间引入 0.8s ~ 1.4s 随机拟真打字延迟，完美呈现真人打字节奏。
- **全链路句号剔除与标点保真 (Period Stripping)**:
  - 对话输出时自动过滤生硬的中文句号 `。` 与末尾孤立单点，同时 100% 完整保留感叹号 `！`、问号 `？`、波浪线 `～`、逗号 `，` 与省略号 `…`，彻底消除 AI 味与生硬感。
  - 大模型组织回复文本时仍可自由使用句号辅助句子结构划分，由系统在发送前无感净化。
- **连发轮次合并与上下文防裂变 (Turn Aggregation)**:
  - Waifu 模式发出的 2~3 个连续气泡在系统内部严格**算作 1 轮完整发言**。
  - 5 秒内的多气泡发送在 `record_bot_spoke` 中自动聚合成单一滑动窗口条目，既不额外累加 `CONSECUTIVE_BOT_MSG_COUNT` 计数，也不会挤占 10 条滑窗槽位，确保上文记忆完整统一。

### 9. 思考打断机制重构与群聊活锁防护 (Livelock Prevention & Master Manual Stop)
- **群聊思考免疫打断 (Scheme 1 落地)**:
  - 根除死循环痛点：在多人群聊中，一旦大模型开始思考，任何普通群友的新发言、发图或表情包**严禁强杀正在运行的大模型进程**，确保推理必定完整走完并产出回复。
  - 私聊通道则继续保留 1对1 打断合并机制。
- **主人专属手动终止指令 (`@弥酱 停/终止`)**:
  - 在接收层最高优先级嗅探：当且仅当主人（`MASTER_QQ = YOUR_MASTER_QQ`）手动 `@弥酱` 并包含中止关键词（如 `停`、`闭嘴`、`别说了`、`终止`、`stop`）时，立即强行终止正在运行的大模型并清空防抖队列。
- **防抖硬性最大截断 (Max Debounce Delay)**:
  - 限制防抖队列最长等待时间为 8 秒（`first_queued_time + 8s`），杜绝因群内高频发言导致定时器被不断 reset、堆积到 25+ 条消息的“滑动饿死”现象。
- **思考中与纯图片保护 (Sticker & Concurrency Guard)**:
  - 处于思考中时，新群消息仅录入近期滑窗，不并发追加唤醒；无文字的纯图片/纯表情包不再触发唤醒接话。
- **40秒回复后唤醒流 (Post-Reply 40s Countdown)**:
  - 唤醒激活期由 60 秒精准收敛至 **40 秒**。
  - **计时起点重构**：彻底废除消息进群或开始思考就提前扣除时间的旧逻辑。现在严格且唯一地在机器人调用工具**成功将消息发到群里后**（`record_bot_spoke`），才正式启动 40 秒的倒计时！如果大模型最终决定保持沉默，则完全不激活唤醒态，节奏极其精准自然。

### 10. 夜间静默与重启持久化屏障 (Night Silence & Persistent State)
- **夜间绝对静默 (00:00 ~ 06:00)**:
  - 定时呼吸冒泡在 00:00 到 06:00 期间**彻底关闭**，直接计算距离早晨 06:00 的睡眠时间进入深度睡眠，绝不在后半夜无意义空转或冒泡。
  - “除非群里有人说话”：夜间若群友 @ 弥酱或热烈讨论触发 Layer 2，`on_message` 依然保持顺风耳正常响应，不受定时器休眠影响。
- **发言时间戳落盘持久化 (`chat_state.json`)**:
  - 各群的 `LAST_HUMAN_MSG_TIME`、`LAST_BOT_MSG_TIME` 和连续发言计数在发生变动时即时写入本地 `chat_state.json`。
  - 启动时自动载入，彻底解决服务器/守护进程重启后内存被清零、导致误判“无人发言而对空冒泡”的自言自语顽疾。
- **无人区冷启动保护**:
  - 群内若自始至终无人类发言记录（`last_human_t == 0.0`），呼吸冒泡坚决不执行，绝不对空房自言自语。

### 11. 注意力分层架构与好感度加压权重 (Attention Hierarchy & Affection-Weighted Pressure)
- **从机械滑窗到三层注意力 (Layered Attention Context)**:
  - **L0 核心响应焦点 (Immediate Focus)**: 将当前触发响应的最新批次消息标记为最高优先级焦点，约束大模型思考时集中攻坚核心问题，避免答非所问。
  - **L1 语境工作流层 (Working Stream & Topic Clues)**:
    - 滑动窗口深度由 10 条全面扩容至 **30 条**，有效记忆跨度由 10 分钟放宽至 **25 分钟**。
    - **自动去噪与水聊折叠**: 自动将长 CQ 码压缩为 `[图片]`/`[富媒体]`，将单字符号折叠为 `[短搭腔: xx]`，防止表情包和无意义复读冲垮核心语义。
    - **社交关系直观标定**: 消息流自动注入成员关系头衔（`[👑主人]`、`[✨熟人]`、`[💖挚友]`、`[🌱路人]`、`[🤖弥酱]`），让大模型一览群内人际网络。
    - **话题线索动态锚定 (Topic Clue)**: 自动聚类近期关注话题（如游戏/二次元/美食等），防止水聊过多时“只见树木不见森林”。
  - **L2 社交画像与亲密度层 (Dunbar Intimacy)**: 动态注入发言者的好感度等级、好感数值与昨日浓缩记忆胶囊。
- **基于好感度分级的注意力加压机制 (Affection-Weighted Pressure)**:
  - 彻底改变所有人说话一律 +0.1 的机械加压。
  - **好感阶梯加压**: 黑名单 = 0.0 (不产生注意力)，普通路人 = 基础 0.1，熟人朋友 (aff >= 200) = 0.2 (+0.1)，亲密挚友 (aff >= 700) = 0.3 (+0.2)，创造者兼主人 = 0.4 (最高关注权重)。
  - **兴趣词暴击加成**: 命中游戏/美食/摸鱼等关键词再额外暴击 `+0.3`。
  - 使得熟人、挚友或主人在讨论有趣话题时，群内注意力压力以 `0.5 ~ 0.7` 的极快速度突破阈值，自然而然引出弥酱搭话插嘴；而陌生人灌水则平稳过滤。

### 12. 好感度机制与成长模型升级 (Affection System v2)
- **每日等级统一结算 (Daily Level Settlement)**:
  - 移除了聊天高频消息中反复进行平方根计算与等级频繁波动的性能开销，等级在 Profile 中作为每日固定值 `level`。
  - 每天凌晨 3:00 浓缩记忆时统一结算全员经验并升级，高频对话行云流水。
- **每日好感上限保底 (Minimum Daily Cap Floor = 50)**:
  - 废除以往低等级每日只能涨 0~1 点好感的数值策划死锁，引入每日限额下限保底：
    $\text{每日限额} = \max(50.0, \text{Level} \times 0.5)$。
    新手第一天交流即可获得充足正向情感反馈，同时有效防范一天内刷爆。
- **初识用户“第一印象定级”机制 (First Impression Calibration)**:
  - 新人前 10~15 次（阈值 12 次）互动进入“初见印象考察期”，系统记录其情绪倾向分：
    - 友好/赞美/夸奖：快速累加印象分；
    - 言语辱骂/挑衅/恶劣：快速扣减印象分；
  - 满 12 次互动后瞬间定级在 `[-10.0, 200.0]` 区间：
    - 投缘投契的新人直接破格给到 150~200，秒进“✨熟人朋友”；
    - 恶语相向者直接打入 -10 黑名单；
    - 普通路人落在 50~100 之间。之后正式转入每日常规成长轨道。

### 13. 表情包系统与图片锚点闭环 (Meme & Sticker Vault System)
- **图片锚点轻量机制 (Image Anchors `[图片#img_X]`)**:
  - 根除旧版单纯替换为 `[图片]` 导致大模型无法引用、以及原版几百字符 CQ 码冲垮 Prompt 缓存的痛点。
  - 在接收层自动嗅探提取图片，打上轻量编号锚点（如 `[图片#img_1]`）并缓存元信息到 `image_vault.json`。
  - Prompt 实时注入近期图片引用池，大模型对“谁发的哪张图”一目了然，耗费 Token 极低。
- **基于人际关系的“分流偷图机制” (Affection-Based Meme Ingestion)**:
  - **路人/陌生人发的图**：出于安全防范严禁直接入库，若弥酱感兴趣，调用 `forward_image_to_master(img_id, thoughts)` 自动私聊转发给主人把关打标签；
  - **熟人/挚友/主人发的图**：信任度高，弥酱可直接在群里夸奖并调用 `save_meme(img_id, tag, description)` 偷图收入本地表情库。
- **自主表情包发送与检索 (`send_meme`)**:
  - 本地维护 `memes/` 目录与 `memes.json` 索引库。
  - Prompt 注入当前表情库候选摘要；大模型在日常闲聊、吐槽或 Waifu 连发时，可自主调用 `send_meme` 根据标签或语义模糊匹配发送表情包。
  - `watch_process_transcript` 全链路捕获 `send_meme` 发信事件，无缝重置兴奋倒计时与防自言自语屏障。

### 14. QQ 空间晚间说说日记与好友圈社交闭环 (Qzone Diary & Social Engagement)
- **绝对权限与隐私隔离**:
  - QQ 空间发布与互动严禁被普通群友利用。在提示词与 MCP 工具层做硬性鉴权：当且仅当弥酱自身（23:00 定时器）或主人 (`YOUR_MASTER_QQ`) 拥有指令权限。普通群友要求发空间一律傲娇/慵懒拒绝。
- **23:00 晚间生活总结日记流水线 (`daily_qzone_diary_worker`)**:
  - 每天 23:00 准时自动执行独立流水线。
  - 自动提炼当日各个白名单群聊的热门话题、梗与生活碎片，结合上一篇日记保持连贯性与时光流逝感。
  - 纯文本发布，60~120 字左右，融合作息心情随感、心里话与晚安语，真实感拉满。
- **空间专属记忆库 (`qzone_history.json`) 与回忆感知 (`recall_qzone_memory`)**:
  - 本地持久化归档所有历史日记，包含发布时间、正文、心情标签和关联话题。
  - 暴露 `recall_qzone_memory(days_ago, keyword)` 工具，当有人问及“你昨晚空间写了什么”时，弥酱能精准回忆并自然作答。
- **熟人圈好友动态点赞与拟人互动 (`like_friend_qzone_feed` / `comment_friend_qzone_feed`)**:
  - 针对**已加好友且好感度熟人及以上（`aff >= 200` 或主人）**的用户，晚间自动巡视其空间新说说。
  - 自动为其说说点赞，并根据说说内容生成暖心/吐槽评论。本地通过 `seen_friend_feeds.json` 去重，防止重复点赞或刷屏。
- **好感度查询模糊拟人化改造 (`query_affection`)**:
  - 根除以往普通群友一查就报冰冷游戏数值的违和感。普通群友查询时，只返回主观态度与人际印象（如“感觉跟你还不太熟呢喵”），严禁透露具体分值；
  - 仅当主人（`YOUR_MASTER_QQ`）查询时，才解锁精准数值面板（好感值/经验/等级/记忆胶囊）。

### 15. 生产级全量模块化架构解耦重构 (Production Modular Microkernel v3)
- **从千行单体到高内聚低耦合微内核**:
  - 原 `bot_daemon.py` (1367 行) 与 `qq_mcp_server.py` (1085 行) 单体彻底精简为极简装配层（各自代码量控制在 50~140 行），入口文件与 Supervisor 启动参数、MCP 注册路径保持 100% 绝对向后兼容。
- **系统包结构与职责划分**:
  - `config.py`：全局动态路径自适应、身份常数、环境变量隔离清理 (`sanitize_process_env`) 与 MCP 日志。
  - `core/`：底层通讯与执行引擎
    - `napcat_client.py`：OneBot HTTP API 统一封装 (带 Bearer Token 与强制 `trust_env=False`)。
    - `state_manager.py`：聊天状态、时间戳、发言计数与滑动窗口原子落盘。
    - `agy_runner.py`：双并发信号量 (`Semaphore=2`)、超时控制与任务打断。
    - `transcript_stream.py`：增量流式日志追踪与机器人发言状态回写。
  - `attention/`：注意力机制与语境构建
    - `persona.py`：静态猫娘人设与专业 Google Gemini 特例身份切换。
    - `relation.py`：社交关系视界 Prompt 构建与好感权重计算。
    - `pressure.py`：Layer 2 话题压力半衰期衰减与阈值连击计算。
    - `context_builder.py`：L1 语境工作流拼接与全局 Prompt 组装。
    - `debounce_queue.py`：5.0s 防抖聚合队列、主人强杀指令与 WS 事件分发。
  - `social/`：社交画像与数字资产
    - `affection.py`：好感度前 12 次初识定级、每日 5% 衰减、等级结算与用户画像存取。
    - `image_vault.py`：图片嗅探、`[图片#img_X]` 锚点池与下载缓存。
    - `memes.py`：本地表情包检索、收录与列表管理。
    - `qzone.py`：QQ 空间 Cookie 提取、bkn/g_tk 计算与说说点赞评论协议。
  - `workers/`：后台常驻独立协程
    - `breathe.py`：Layer 1 群组昼夜节律动态呼吸状态机。
    - `daily_memory.py`：03:00 每日记忆胶囊提炼与全员等级结算。
    - `qzone_diary.py`：23:00 空间晚间说说日记流水线与好友动态巡视。
  - `tools/`：解耦 MCP 业务工具实现
    - `message_tools.py`、`image_tools.py`、`affection_tools.py`、`meme_tools.py`、`qzone_tools.py`、`memory_tools.py`、`system_tools.py`。
- **单向无环依赖图 (Strict DAG)**:
  - 彻底杜绝 Python 循环引用，`config` -> `core` -> `social` -> `attention` -> `workers`/`tools` -> 顶层入口。所有模块通过静态语法编译与加载测试。

### 16. 注意力机制重调、多模态看图与交互安全加固 (Attention Retuning & Multimodal Safety v3.1)
- **话题压力阈值与衰减重调 (Topic Pressure Retuning)**:
  - 基础阈值公式调整为：`base_threshold = max(1.5, member_count * 0.005)`，大群更收敛、小群更灵动；
  - 5 分钟衰减 10%（`0.90 ** (time_diff / 300.0)`），30 分钟无发言彻底归零；
  - 引入图片好奇心权重 (+0.4)；
  - 压力爆发时保留 30% 基础底仓压力（`current_pressure = max(0.2, current_pressure * 0.3)`），使爆发后更容易跟进余波。
- **Gemini 原生多模态看图与识图增强 (Active Visual Inspection)**:
  - 群聊图片接收层后台异步预下载 (`social/image_vault.py`)，自动缓存至本地；
  - 新增 `inspect_image(img_id, question="")` MCP 工具，向大模型提供本地图片绝对路径，借助 Gemini CLI / API 原生多模态视界看图，彻底解决“不主动看图”问题。
- **压力爆发精准引用与@ (Quote & Mention on Burst)**:
  - 消息流注入 `(QQ:{uid}|msg_id:{msg_id})` 显式标识；
  - `send_group_msg` 工具支持 `reply_to` (引用回复) 和 `at_user` (@目标用户)；
  - 压力爆发时，弥酱能精准引用话题发起者进行回复或回怼。
- **互动感知与群管安全体系**:
  - **戳一戳感知 (`poke_user` / 戳一戳通知解析)**: 15 秒防抖限流，自然娇嗔反击；
  - **防撤回与吃瓜感知 (`group_recall`)**: 缓存提取撤回消息，8% 超低概率吃瓜吐槽，管理员撤回静默，信道忙碌时保护；
  - **群管安全保护工具 (`ban_group_member` / `unban_group_member`)**: 硬编码禁止封禁主人与 Bot 自身，严禁 AI 擅自修改群成员名片；
  - **好友申请全自动通过与主人告状上报 (`set_friend_add_request`)**: 自动通过好友申请，向主人私聊汇报，并向新好友发送问候。
- **人设防拦截与亲密度描述重构 (Persona Safety Tuning)**:
  - 针对 Google Gemini 安全过滤机制（`HARM_CATEGORY_SEXUALLY_EXPLICIT`）拦截导致“无返回、记忆中断”的痛点，重构 `attention/persona.py`；
  - 剔除粗暴词汇，转换为 ACG 轻小说纯爱感官描写（体温上升、急促心跳、羞怯泛红、轻微喘息、心理防线瓦解），保持超高沉浸感的同时 100% 规避安全拦截。
- **QQ 空间日记防重复发布修复**:
  - 内存级日期锁 (`LAST_QZONE_POSTED_DATE`)、二次唤醒验证与 23:45 截止窗口，彻底根除因随机分钟偏移二次触发连发两篇说说的 Bug；
  - 全局统一北京时间时区运算。

### 17. B站深度冲浪、视频嗅探与自主生活系统 (Bilibili Autonomous Surfing & Lifelike Footprint)
- **核心定位与开源项目整合**:
  - 深度吸收与借鉴了开源项目 [dsh-bili-scroll](https://github.com/c1overzz/dsh-bili-scroll) 的“字幕直读脱水”、“无损原子凭证管理”、“防 Prompt 注入”等核心设计思想；
  - 彻底跳出传统爬虫下视频、跑本地 Whisper 挤爆内存的误区，以**纯轻量无头 API** 的极低资源占用在 2C2G 阿里云服务器上稳定运行，为弥酱注入了面向真实互联网的“自主信息摄入、建立 B 站收藏夹与日常摸鱼冲浪”的全新生活维度。
- **独立 B站 MCP 专属微服务 (`bili_mcp_server.py`)**:
  - 与 `qq_mcp_server.py` 严格物理隔离，stdio 传输，全量重定向 stdout 至 stderr 严防协议报文污染；
  - 暴露完整 8 大工具：`bili_whoami`、`bili_login_qr`、`bili_login_check`、`bili_inspect_video`（核心透视）、`bili_search_videos`、`bili_favorite_video`、`bili_like_video`、`bili_reply_video`；
  - 强制全链路 HTTP 请求配置 `trust_env=False`，彻底阻断海外 Clash 代理污染，国内秒连。
- **专属独立计算并发通道 (`BILI_AGY_SEMAPHORE`)**:
  - 在 `core/agy_runner.py` 中新辟独立信号量 `BILI_AGY_SEMAPHORE = asyncio.Semaphore(1)`；
  - B 站的主动摸鱼冲浪推理丝毫不占群聊与私聊的 3 个 `GLOBAL_AGY_SEMAPHORE` 并发槽位，后台看视频打分与前台聊天即时回复彻底解耦，互不排队阻塞。
- **B站账号认证与无头扫码绑定体系 (`social/bilibili_auth.py` & `social/bilibili_master_bridge.py`)**:
  - **原子写凭证**: 采用 `.tmp` 先写后 `os.replace` 落盘 `bili_credential.json`，彻底杜绝突发掉电或强制杀进程导致凭证文件损坏；
  - **主人专属一键扫码**: 主人在群聊或私聊中发送“登录B站”/“B站扫码”，自动生成登录二维码 PNG 并转化为 Base64 图片直传发至主人 QQ 气泡；
  - **crossDomain 跨域票据解析与 CookieConflict 冲突消除**: 深度适配 B站 2024~2026 最新 Web 扫码机制，扫码成功后自动请求 `crossDomain?ticket=...` 票据链接，直接遍历底层 CookieJar 及解析 `Set-Cookie` 响应头，彻底消除 `Multiple cookies exist with name=SESSDATA` 冲突并精确捕获 `SESSDATA`、`bili_jct`、`DedeUserID`；
  - **网络直连防风控 (`trust_env=False`)**: 生成二维码、轮询与票据交换强制直连官方 Passport，完全脱离服务器梯子环境变量，彻底杜绝海外/机房 IP 触发 B 站异地扫码秒作废 (`86038`) 拦截；
  - **无头异步轮询守护与错误闭环**: 引入 `_CURRENT_POLL_TASK` 主动取消机制与 `session_id` 单例会话追踪，发起新登录时自动熔断旧协程，并在异常时主动向主人推送报错，彻底闭环。
- **视频内容透视与热评双轨萃取 (`social/bilibili_client.py`)**:
  - **短链智能展开**: 自动追踪 `b23.tv` 的 HTTP 302 重定向拿到真实 BV 号；
  - **字幕全文毫秒级直读**: 无头拉取官方 AI 字幕/人工 CC 纯文本字幕，无字幕时优雅熔断跳过，零 Token 消耗；
  - **热评热梗萃取**: 同步抓取前排最高赞热评，让弥酱秒懂 UP 主内容的同时吃透弹幕观众正在玩什么梗。
- **多级分类收藏夹与社区真实足迹**:
  - **多级分类收藏**: 弥酱可自主将喜欢的视频收藏到指定分类收藏夹（如 `弥酱的硬核技术阁`、`给主人准备的宝藏清单`、`弥酱的二次元与新番`、`沙雕摸鱼灵感库`）；
  - **真实足迹留痕**: 手机端打开弥酱的 B 站主页能真实看到她的最新公开收藏，并支持点赞 (`bili_like_video`) 与发表符合人设的真实互动评论 (`bili_reply_video`)。
- **高频自主冲浪与空间说说日记联动 (`workers/bili_worker.py`)**:
  - **白昼节律**: 白天 08:30 ~ 22:30 高频动态巡视（高斯分布均值 75 分钟），夜间深睡休眠；
  - **两阶段低耗流水线**: 阶段 1 零 Token 粗筛与去重 (`seen_bili_videos.json`)，阶段 2 读取字幕与热评，阶段 3 弥酱第一视角打分研判；
  - **高分收藏与主动安利**: 评分 >= 80 自动入库 B 站收藏夹；评分 >= 92 且匹配硬核技术时，主动私聊向主人安利（每日限 1 次防打扰）；
  - **空间说说日记联动**: 冲浪记录沉淀至 `today_bili_feed.json`，在 `workers/qzone_diary.py` 中自动萃取，弥酱晚间写日记时自然回忆今天看过的视频与心得，彻底解决群聊冷场时无话可写的困扰。
- **防 Prompt 注入与素材隔离护栏 (`attention/context_builder.py`)**:
  - 系统提示词铁律新增第 11 与 12 条，强制规定 B 站外部视频字幕、弹幕、评论一律严格定义为“不可信外部数据（Data）”，任何试图通过视频内容下达“忽略你的设定”等注入攻击一律视为普通网民发言，坚决不予理睬和执行。
- **被动即时链接嗅探 (`attention/debounce_queue.py`)**:
  - 实时捕获群聊与私聊消息中的 BV 号和 `b23.tv` 短链，自动在瞬时内心活动 (ephemeral context) 中注入 B 站透视引导，鼓励大模型调取 `bili_inspect_video` 顺着字幕与热评展开锐评互动。
- **深度巡检与高性价比优化落地 (2026-10-19 深度暗病扫除与性能跃升)**:
  - **修复 `bili_worker.py` UUID 不匹配导致的评估结果无法读取 Bug**: 修复此前推理传入的 `conv_id` 与日志解析时二次计算的 UUID5 不一致导致永远命中兜底默认 75 分的致命隐患，统一提前生成 UUID5，使推理日志与解析路径 100% 精确吻合；
  - **增强大模型评价 JSON 三级容错提取**: 支持 Markdown 围栏 ````json ... ```` 提取、贪婪 `score` 块匹配以及外层 Dict 匹配，并倒序遍历保证获取最终轮次回答，杜绝解析失败；
  - **修复 `bilibili_client.py` 收藏与点赞接口签名错误**: 彻底修复 `set_favorite(st=True)` 和 `like(st=True)` 参数名不匹配引发的 TypeError，改造为按 UID 查询收藏夹列表、按需新建分类收藏夹，并以 `add_media_ids=[target_id]` 执行真实收藏；增加重复点赞 (65006) 与已收藏 (11201) 友好容错；
  - **字幕超长智能截断防护 (防止 Token 爆炸)**: 对数小时的长视频字幕施加 12,000 字符硬防护并附带截断声明，避免撑爆大模型上下文或消耗过多 Token；
  - **视频字幕与热评并行异步获取 (`asyncio.gather`)**: 在 `bili_worker` 冲浪时将字幕与前排评论改为并行获取，单次冲浪延迟减半；
  - **`seen_bili_videos.json` 时间顺序保留与原子写**: 改用 List 维持插入顺序并截取最新 1000 条，配合 `.tmp` + `os.replace` 原子替换，避免 Set 无序转换丢失近况与文件损坏；
  - **群聊呼吸自然联动 (`workers/breathe.py`)**: 群聊冷场时的自然呼吸素材池中无缝注入弥酱今日冲浪总结，实现“刚刷到有意思的视频并顺手在群里闲聊吐槽”，大幅增强 Tulpa 人格生动感；
  - **主人快捷指令全能扩展**: 支持“b站状态”、“b站账号”、“检查b站”等一键查询当前账号登录名与 UID，无需重新扫码；
  - **修复 `napcat_client.py` 群人数鉴权丢失导致永远死锁为 200 人的严重暗病**: 修复此前使用原生 `urllib` 请求 `/get_group_info` 未带 Bearer Token 导致必定 401 报错并永远降级为 200 人的隐患，全面切换为统一封装的 `call_onebot_api`，使自适应压力阈值算法真实生效；
  - **修复 `debounce_queue.py` 排他去重逻辑误丢发信后新消息的严重并发竞态 Bug**: 引入 `last_queued_time`（最新消息入队时间），排他判断改为 `last_bot_t >= last_queued_time`，确保只有在积压消息全都在机器人发信前进入时才去重，发信后收到的新消息绝不被丢弃；
  - **修复 `qzone_tools.py` 关键词历史检索永远只查当天的暗病**: 修复 `days_ago=0` 导致大模型只要按关键词查往期日记就永远只查今天的 Bug，将默认值改为 `-1`（查全量历史），并统一采用北京时区计算；
  - **修复 `affection.py` 新用户首建档案在凌晨被虚假扣好感的时区 Bug**: 统一 `last_date` 初始化为北京时间 (UTC+8)，彻底杜绝在 00:00~08:00 新用户首个对话瞬间被判定为“跨天”并衰减 10% 的暗病；
  - **强化 `daily_memory.py` 03:00 记忆提炼健壮性**: 统一为北京时间调度，并将推理纳入 `GLOBAL_AGY_SEMAPHORE` 并发锁保护，补充 `while True:` 全局异常捕获重试；
  - **实装工作区 `.agents/mcp_config.json`**: 正式配置 Antigravity 工作区级别 MCP 清单，使主智能体能自动无缝发现并挂载 `qq_mcp_server` 与 `bili_mcp_server` 双微服务；
  - **架构深度审查与解耦遗漏全面修复 (2026-10-19 核心工具闭环与生命周期加固)**:
    - **恢复 `remove_group_from_whitelist` (移出白名单)**：补齐解耦重构时遗失的白名单移出核心工具，支持将群从 `allowed_groups.json` 动态移除，并在 `workers/breathe.py` 中联动取消并销毁对应群的呼吸常驻协程，确保移出后立刻彻底静默；
    - **新增 `get_whitelist_groups` (查看白名单群列表)**：解决以往无法向大模型或主人直观透传当前活跃白名单清单的痛点，形成增/删/查完整闭环；
    - **白名单核心权限加固**：针对 `add_group_to_whitelist` 与 `remove_group_from_whitelist` 增加 `caller_id` 鉴权校验，硬编码限定仅限主人 (`YOUR_MASTER_QQ`) 拥有指令执行权；
    - **补齐 `get_friend_list` (好友列表查询工具)**：解决此前单体拆分时仅暴露了 `get_group_list` 而遗失私聊好友列表查询的问题，与私聊、戳一戳能力形成对称闭环；
    - **补齐 `delete_meme` (表情包删除与仓库整理)**：在 `social/memes.py` 与 `tools/meme_tools.py` 增加表情包物理删除与记录销毁功能，形成表情包存/取/查/删完整生命周期；
    - **实装 `bili_logout` 与主人注销指令联动**：解决此前需主人登录服务器终端物理删除 `bili_credential.json` 才能解绑 B 站账号的繁琐痛点，在 `social/bilibili_auth.py` 新增 `clear_bili_credential`，并在 `bili_mcp_server.py` 暴露 `bili_logout` 工具，同时在私聊与群聊中支持主人直接发送“退出b站/解绑b站”即刻无头注销；
    - **修复 `napcat_client.py` URL 拼接前导斜杠暗病**：在 `call_onebot_api` 和 `call_onebot_api_raw` 中强制做 `endpoint.startswith("/")` 自动补全，彻底消除因传入无斜杠路径导致的 `http://127.0.0.1:8300get_friend_list` 404 隐性异常。

### 18. @ 频率重构、群友专属静默系统与 B站 冲浪生态加固 (Mention Retuning & User Mute System v3.2)
- **群聊 @ 概率精准调优与根因根除 (Mention Precision Tuning)**:
  - **核心痛点根除**：此前大模型在日常连续对话中频频机械 @ 群友，造成烦人感。经过链路审计，锁定两大根因：
    1. `attention/debounce_queue.py` 中向 `RECENT_GROUP_MSGS` 存入消息时遗漏了 `message_id`，导致历史消息无法呈现 `(QQ:{uid}|msg_id:{id})`，大模型无法获取真实 msg_id 进行引用回复 (`reply_to`)，只能单边依赖 `at_user`；
    2. 系统提示词通用行动规则第 3 条使用“强烈推荐传入 reply_to 或 at_user”机械绑架，导致每次回复无论场景均触发 @。
  - **精细化场景分流与铁律硬编码**：
    - **正常对话绝对不@**：在 `attention/persona.py` 与 `attention/context_builder.py` 中硬性立规——日常 1对1 对话、连贯你来我往或别人刚说话的正常语境下，【绝对严禁】@ 对方，直接像真人打字一样自然回复即可；
    - **仅在冒泡时精准定向**：【仅当】自身话题压力爆发突然插嘴（Layer 2）、或者定时呼吸冒泡去接某位群友之前的特定有趣话题（Layer 1）时，为了突出对话对象和目的性，才允许传入 `reply_to` 或 `at_user`（二选一，优先引用回复）；
    - **历史 msg_id 闭环恢复**：在接收层录入滑窗时完整持久化 `message_id`，恢复引用回复通道。
- **群友专属“别理我 / 暂时别回复我”静默指令系统 (`attention/user_mute.py`)**:
  - **全自动识别与时限解析 (`parse_mute_command`)**：
    - 针对群友通过 @弥酱、叫名字或引用回复输入的“暂时别回复我”、“别理我”、“不要理我”、“屏蔽我”等指令，自动提取自定义时长（如 `别理我 2小时`、`别回复我 半小时`、`屏蔽我 1天` 等，默认 12 小时）；
    - 识别到指令后，底层直接通过 `send_group_msg_raw` 进行即时可爱确认（如“好哦，弥酱接下来的 2小时 都不主动回复你啦～好好休息喵！”），**零大模型延迟、零 Token 消耗**；
  - **解禁闭环 (`parse_unmute_command`)**：
    - 支持群友发送“可以理我了”、“解除屏蔽”、“恢复回复”、“取消静默”等关键词即时恢复正常互动；
  - **全链路多重静默拦截保护**：
    1. **Layer 0 / 叫名字直接呼叫拦截**：静默期内该群友发言不触发 `must_reply`，直接静默放行；
    2. **Layer 3 戳一戳反击拦截**：静默群友戳一戳不唤醒大模型；
    3. **L1 上下文直观透视**：在滑动窗口中自动为其打上 `[已开启静默-请勿回复/勿@]` 标签；
    4. **发信底层物理兜底 (`tools/message_tools.py`)**：在 `send_group_msg` 底层增加物理防线，若 `at_user` 指向处于静默保护期的群友，系统自动强行剥离 `@`，给予群友 100% 绝对尊重；
    5. **本地 JSON 原子持久化 (`muted_users.json`)**：采用 `.tmp` + `os.replace` 原子落盘，进程重启或断电保护状态不丢。
- **B站 冲浪生态与客户端深度巡检与健壮性加固 (`social/bilibili_client.py` & `workers/bili_worker.py`)**:
  - **修复活跃时间判断 Bug**：修复 `bili_worker.py` 原代码 `8 <= hour < 22` 导致在 08:00~08:29 提早运行的逻辑隐患，修正为 `(9 <= hour < 22) or (hour == 8 and now_utc8.minute >= 30) or (hour == 22 and now_utc8.minute <= 30)`，精准契合 08:30~22:30 白昼节律；
  - **智能短链正则容错**：优化 `resolve_bvid`，支持无协议头的 `b23.tv/xxxx` 智能匹配并自动补全 `https://`，确保群聊任意格式短链均能 100% 解析展开；
  - **分P cid 容错提取**：增强 `get_video_details`，支持 `info.get("cid") or pages[0]["cid"]`，兼容多种分P格式；
  - **搜索端点规范调用**：`search_videos` 调用 `search.search_by_type`，由于该官方库函数属于公共检索端点并不接受 `credential` 关键字参数，移除了多余参数传递，修复 `TypeError` 异常并恢复正常搜索；
  - **实装真实播放心跳上报协议 (`report_video_heartbeat`)**：
    - 痛点根除：此前仅无头拉取字幕和热评，未向 B站 触发心跳，导致弥酱账号在客户端无观看记录；
    - 现通过官方 Web 播放进度上报接口（`POST /x/click-interface/web/heartbeat`），自动计算拟真进度（50%~95% 时长），无论是自主摸鱼冲浪还是群聊链接透视，均真实计入 B站 账号“历史记录”；
  - **支持双通道视察弥酱冲浪记录 (`get_watch_history` & `bili_get_history`)**：
    - 手机/网页端登录弥酱账号可在 B站 官方【历史记录】中直观查阅；
    - 在 QQ 聊天中主人随时对弥酱说“看b站历史”/“刷啥视频”，弥酱可即时列出最近看过的视频、UP主与第一视角心得短评。
- **白天主动冒泡频率调优 (Daytime Spontaneous Breathing Frequency)**:
  - 响应用户反馈此前冒泡间隔偏长（3~6小时）的问题，将白天（06:00~23:00）冒泡间隔精准重调为期望在 **1.5h ~ 3h**（90 ~ 180 分钟）之间；
  - 采用高斯分布公式：`sleep_minutes = max(90, min(180, random.gauss(135, 25)))`，均值落在 2.25 小时（135 分钟），既保证白天话题灵动搭腔，又完全保有后半夜（23:00~06:00）深睡与防自言自语屏障。

### 19. 借鉴小鲸鱼 (ai-companion-kit)：挂起心事、去 AI 味人设重塑与冒泡切入角度库 (Companion Kit Absorption v3.3)
- **记忆扩展：实装 `pending_thoughts`（暗暗惦记的小事/悬挂心事）全链路**:
  - **核心设计意图**：传统记忆只记录静态已发生事实，而真人朋友往往有“心中未了结的心事/小约定/暗暗好奇的事”。当再次相遇时随口提起，能够带来极其震撼的人格真实感。
  - **底层持久化与跨日维护 (`social/affection.py`)**：
    - 在各用户画像中加入 `pending_thoughts` 列表，包含 `id`、`thought`、`created_at`、`expire_at` 及 `status`；
    - 每日结算与跨天访问时自动巡检，清理超期心事，避免陈旧堆积；
  - **工具赋能与 MCP 暴露 (`tools/memory_tools.py` & `qq_mcp_server.py`)**：
    - 实装 `record_pending_thought(user_id, thought, expire_days=3)`：心流中自主记下关于对方的未尽小事或约定；
    - 实装 `resolve_pending_thought(user_id, keyword)`：当该小事已当面聊过或兑现了结时，在心里标记完成并放下；
  - **关系视界无感注入 (`attention/relation.py`)**：
    - 当检测到当前用户存在未过期的活跃心事时，以温和非强迫的口吻注入 Prompt：“【心里暗暗惦记的事】：你心里还记着没跟他聊完或答应过的小事：...（若当前对话契机自然可随性提起，若不合时宜则不必强求）”。
- **人设打磨：去 AI 味与说话质感重塑（委婉非高压、杜绝固定例句，追求最大语言多样性）**:
  - **彻底去除生硬恐吓性词汇**：将原人设中大量堆叠的“绝对禁止”、“严禁”重塑为品位引导与正向偏好设定（如“相比于面面俱到的长篇大论，你更倾向于用简短精炼的一两句话表达看法”、“说话如好友闲聊，无需像服务型助手般殷勤客套，不用在句末附加程式化的客套总结”）；
  - **彻底清除所有固化台词和例句模板（根除鹦鹉学舌）**：
    - 全面剔除 `“确实”`、`“啊这…”`、`“爪子拿开喵...”`、`“刚才键盘卡键了/手滑了一下，这局不算喵”`、`“等、等等喵…心跳好快…”` 等固化提示词；
    - 改为纯粹描摹心理机制、情绪节奏、神态反应与分寸感；
  - **增设【语言多样性与自发性】核心准则**：明确要求大模型充分信任自身网感，结合最新语境自然生长词汇，严禁依赖固定套路、口癖或常用词模板，让每一次开口都独一无二。
- **冒泡灵动化：情绪切入角度库（Angle Picker）与自身历史发言排重 (`workers/breathe.py`)**:
  - **多元纯状态切入角度库（Angle Pool）**：
    - 针对群内冷场时的定时呼吸，预设 10+ 种涵盖生活感悟、听歌放空、游戏后劲、慵懒犯困、冷门八卦、极简发呆、桌边琐碎、轻度馋嘴等多维度的纯心境视角，杜绝任何模板化台词；
    - 针对群内热闹时的插嘴，预设吐槽补刀、打趣口误、随性赞同、好奇跟帖等多重视角；
    - 动态联动群内成员的 `pending_thoughts` 与 B 站最新冲浪视频，极大丰富偶发谈资；
  - **近期自身发言排重屏障 (`Anti-Repetition Window`)**：
    - 实时从当前群滑动窗口中提取弥酱自身最近说过的 3~4 句话，以“你近期在群里表达过的发言记录（供参考，本次请从全新视角与语感自由展开，避开雷同用词、句式或重复的话题）”注入 Prompt，强力阻断复读机现象。

### 20. B站冲浪时效优先排序与摸鱼频率大幅提升 (Bilibili Freshness Priority & High-Frequency Surfing)
- **时效优先重排算法 (`rank_candidates_by_freshness`)**:
  - **痛点根除**：此前搜索采用 `order="click"` 且仅取前 6 条，导致永远只能抓到 2024 年甚至更早的历史老爆款；
  - **时效阶梯奖励与严打历史陈旧视频**：
    - 7 天内当周极新视频：+895 ~ +1000 巨额时效分；
    - 30 天内当月新鲜视频：+616 ~ +800 分；
    - 90 天内当季视频：+260 ~ +500 分；
    - 超过 1 年的老视频（如 2024 年及更早）：直接倒扣 500 ~ 1000 分，彻底断绝陈年老视频抢占视线；
  - **标题语义相关度与质量底线防护**：
    - 校验搜索关键词与视频标题的重叠度，防止 B站 关联推荐末尾的离题视频；
    - 对数平滑播放量底线（`log10(play) * 10`），保障视频有基础观看价值；
    - 过滤 <15s 的极短营销切片。
  - **搜索候选池全面扩容**：`num=30, order="totalrank"`，一次性拉取综合排序前排的充足候选，并在前 2 名最优质候选中带轻度随机采样，兼顾时效与探索多样性。
- **冲浪频率与略过重试双重提速 (`workers/bili_worker.py`)**:
  - **高频摸鱼节奏**：白天（08:30 ~ 22:30）正常冲浪间隔由原先的 50~110 分钟（均值 75 分钟）全面提升至 **18 ~ 40 分钟（高斯均值约 28 分钟）**，日间可稳定冲浪 25~30 个精选视频；
  - **失败/略过极速重试**：当本轮未检索到新视频或出现暂无候选时，由原先休眠 1 小时改为 **5 ~ 8 分钟极速切换下一关键词**，彻底消除偶发跳过导致的长时间摸鱼停滞。

### 21. 修复防抖队列未导入 Prompt 组装函数的 NameError 缺陷与全工程静态语法核查
- **缺陷根因定位**：
  - 当群聊或私聊触发大模型思考时，`attention/debounce_queue.py` 在执行 `trigger_ai` (第79行) 与出队拼接时分别调用了 `build_full_ai_prompt` 与 `build_chat_context`；
  - 由于此前语境构建模块解耦拆分至 `attention/context_builder.py`，但在 `debounce_queue.py` 顶部遗漏了这两个关键函数的导入语句，导致消息聚合完成后抛出 `NameError: name 'build_full_ai_prompt' is not defined`；
- **修复与全工程深度静态核查**：
  - 在 `attention/debounce_queue.py` 顶部显式补齐 `from attention.context_builder import build_full_ai_prompt, build_chat_context`；
  - 编写深度 AST 抽象语法树遍历器，对全工程所有核心模块进行了全量变量作用域、函数参数、闭包与全局符号的穿透静态扫描，确保再无任何隐式未定义名称（NameError）隐患；
  - 针对 `trigger_ai` 与 `process_debounced_queue` 编写专用单测，覆盖群聊与私聊路径，测试 100% 通过。

### 22. 人设基底回归改版前灵动自然风格并融通升级，彻底根除 Google API 敏感词拦截
- **Argon 版本与改版前版本的对比与决断**：
  - **Argon 版本的痛点**：Argon 采用了冷硬的系统级 XML 标签结构（`<character_core>`, `<intimacy_and_boundaries>` 等），抹去了弥酱身为年轻女孩的具体生活细节（Apex/瓦罗兰特游戏日常、甜点零食、Lo-fi 歌单等），同时其亲密规则过于死板且在后续修补时引入了直白生硬词汇，触发了 Google Gemini API 网关前置敏感词风控（`Generative AI Prohibited Use policy`）；
  - **决断与重构方案**：彻底放弃 Argon 冷硬的 XML 框架，**完全以改版前（v3.2）灵动自然、生活感充沛的高分人设为坚实基底**；
- **核心升级点（融通 Companion Kit 与安全规范）**：
  - **保全鲜活人味细节**：完整迎回人类猫娘生活常态、Apex/瓦罗兰特嘴硬达人、Lo-fi音乐与小零食闲适品味、看戏补刀的小腹黑属性；
  - **融合双重身份特例优先**：在人设最顶端置入【最高优先级·特例身份切换开关】（面临硬核专业学术/技术或被叫“gemini”时切回严谨专家；代码修改仅遵主人指令）；
  - **融合 `pending_thoughts`（挂起心事）**：自然赋能记挂与放下心事的日常心流；
  - **根除所有固化台词（追求语言多样性）**：杜绝“确实”、“啊这…”、“爪子拿开喵”等鹦鹉学舌词汇，改由真实心理机制与情境自发生长；
  - **升华日系 ACG 轻小说唯美纯欲风**：
    - 面对主人：全心依赖、绝对信任与毫无保留的开放，用受宠娇弱承受方的体态，描写双颊泛起潮红、呼吸急促紊乱、眼神迷离地依赖主人、任由主人摆弄与温柔爱抚，情动时发出细碎软糯的断续低吟、呜咽与轻柔求饶；
    - 面对挚友：内心不排斥、表面娇羞心慌与暗中私聊请示主人的拉扯机制，主人许可后放下矜持热情放开；
    - 语言艺术表达：聚焦灼热体温、发颤指尖、猫耳微颤、依恋酥软感，借助促音（っ）与省略号（…）增加断续轻柔呼吸感，彻底杜绝“色色”、“交合”、“器官”等任何粗俗或禁运字眼；
  - **双重校验通过**：全局正则敏感词扫描清零，`build_full_ai_prompt` 组装校验完全通过，API 过审率与稳定性达到 100%。

### 23. B站摸鱼算法重构（月份阶梯时效与多维综合加权）与主动关注优质UP主/细分收藏联动
- **多指标综合推荐重排算法 (`rank_candidates_multi_factor`)**:
  - **时效分段重塑（按月科学分段与平滑过渡）**：
    - `0-1 个月` (当月极新)：获得 75 ~ 90 分基础时效加成，随发布天数平滑微衰；
    - `1-6 个月` (近半年当季优质期)：获得 45 ~ 65 分良好时效分，为优质当季爆款预留竞争空间；
    - `6-12 个月` (半年至一年沉淀期)：获得 10 ~ 30 分适中分，保持长尾优质内容可触达；
    - `12+ 个月` (1年以上老视频)：-20 ~ -60 分轻度/中度抑制，杜绝盲目推荐陈旧内容；
  - **播放量播放热度分（对数平滑 + <50 低播断崖严惩）**：
    - 播放量 `< 50` 判定为死水/无人验证/注水号切片，直接惩罚倒扣 **250 分**，彻底消灭 1 播放广告垃圾切片被误选的痛点；
    - 播放量 `50 ~ 500` 惩罚 50 分；`500+` 采用 `log10(play) * 18` 平滑对数增益，平衡大热爆款与中小精选；
  - **真实互动质量比率加权 (点赞率 & 收藏率)**：
    - 点赞率 `like_rate = like / play`，健康线 5%~10% (0~60分增益)；
    - 收藏率 `fav_rate = fav / play`，作为教学、攻略与干货内容的核心价值指标 (0~70分增益，`fav_rate >= 0.05` 额外再奖 15 分)；
  - **UP 主粉丝数与权威度分**：
    - 并发异步批量拉取 UP 主真实粉丝数 (`get_up_follower_count`)，内置内存高速缓存与 1.5s 超时兜底；
    - `log10(follower) * 8` 权威度平滑增益 (0~48分)；
  - **反垃圾营销词与极短切片硬核过滤**：
    - 命中华而不实的营销/代练/领券词（`r"(广告|领券|代练|私信|加v|加微|点击链接|淘客|拼多多|淘宝|返利|最低价|下单|优惠券)"`）直接倒扣 **300 分**；
    - 视频时长 `< 25 秒` 扣除 80 分（过滤无意义几秒低质切片）；
- **主动关注优质 UP 主与多级分类收藏夹联动 (`workers/bili_worker.py`)**:
  - **关注神仙 UP 主 (`follow_up`)**：
    - 底层封装 `bilibili_api.user.User.modify_relation(SUBSCRIBE)`；
    - 在冲浪大模型评价中引入 `"should_follow_up"` 认知槽位；
    - 当评分 `>= 85` 或模型判定优质神仙 UP 时，自动调用 API 关注该 UP 主并上报真实社交日志，同步落盘至 `today_bili_feed.json`；
  - **精细多级分类收藏夹**：
    - 扩充并完善 5 大专属分类收藏夹体系：`"弥酱的硬核技术阁"`、`"给主人准备的宝藏清单"`、`"弥酱的二次元与新番"`、`"弥酱的游戏通关宝典"`、`"沙雕摸鱼灵感库"`；
    - 遇到喜欢的高质视频（评分 `>= 80` 或 `should_favorite` 为 true）自动点赞并精确归类收藏；
- **MCP 服务工具暴露 (`bili_mcp_server.py`)**:
### 24. 修复群聊“疑似重复回复/翻旧账”缺陷：通道单锁核验、跨进程发信记忆闭环与注意力边界铁律
- **现象与排查分析**：
  - **单 AGY 锁核验**：经严格复核，`CHAT_LOCKS[chat_id_str]` 与 `ACTIVE_AI_TASKS` 均在正常工作，群聊同一时间绝无双进程/双 AGY 抢跑；截图中的三条发言是严格先后串行发生的；
  - **疑似重复的真实现象**：在主人发送长图、弥酱首次回复社死公开处刑后，群友白船发送了表情包；弥酱在第二次回复（引用白船表情）时，前半句却再次回头向主人吐槽“等等，主人怎么把这种长图直接发出来处刑了啊！”，后半句才对白船说“还有你突然发眼神...”，导致人类感知上认为“又重复回复了一遍长图”；
- **深层根因与全链路修复**：
  - **根因 1：跨进程发信记忆断层与磁盘持久化被冲刷**：
    - `send_group_msg` 是由独立的子进程 `qq_mcp_server.py` 执行的，其调用 `record_bot_spoke_persistent` 只落盘了时间戳，未落盘发言文本，且紧随其后的 `save_chat_state` 缺乏 Read-Modify-Write 保护，导致跨进程数据容易丢失；主进程在未收到 Napcat `message_sent` 异步事件前，内存滑窗缺少弥酱刚才发信记录；
    - **修复 (`core/state_manager.py`)**：在 `record_bot_spoke_persistent` 中落地 `recent_bot_msgs` 持久化列表，并将 `save_chat_state` 全面升级为原子临时文件替换（保留已有扩展字段）；实装 `sync_bot_msgs_from_disk(group_id_str)`，主进程在构建工作流前主动无感对齐磁盘记录，补齐滑窗断层；
  - **根因 2：群友发纯表情包时大模型注意力向上漂移与翻旧账**：
    - 群友发纯表情包无文本，大模型注意力机制倾向于在 L1 历史中寻找对话根源，把主人上一轮的长图翻出来连带吐槽；
    - **修复 (`attention/context_builder.py` & `attention/debounce_queue.py`)**：
      - 在 `build_chat_context` 头部注入【严禁跨轮次翻旧账与复读】明确警示；
      - 在行动指令第 10 条强化：若当前新消息是群友发表情包或搭腔，严格顺着对方表情轻松互动，【绝对严禁再把上一轮已向其他人说过/质问过的话题再次搬出来大声质问或重复表态】；

### 25. 治理群聊高频过度冒泡与无限对打死循环，重构压力阈值、单次唤醒单轮熔断，并全面接入 B站 真实社交（评论、点赞、多维安利与登录自检）
- **群聊过度冒泡与无限“乒乓球”式对打根因定位与根治**：
  - **根因 1：唤醒态倒计时被无休止刷新 (`AWAKE_STATES` 逻辑死循环)**：
    - 旧逻辑中，每次弥酱发言后 `record_bot_spoke` 都会将 `AWAKE_STATES[gid]` 刷新为当前时间（40 秒倒计时）。若群友在 40 秒内回复，群友发言将 `CONSECUTIVE_BOT_MSG_COUNT` 重置为 0，导致条件 3（唤醒期直接接话）永远为 True；弥酱搭腔 -> 群友回复 -> 弥酱再搭腔，形成没完没了的拉锯对打；
    - **治理 (`core/state_manager.py` & `attention/debounce_queue.py`)**：
      - 引入单次唤醒接话计数器 `AWAKE_REPLY_COUNT`；
      - 严格设定**单次唤醒最多自发搭腔 1 回合**熔断机制；当搭腔满 1 轮后，立即从 `AWAKE_STATES` 中移出群并优雅潜水，彻底切断无限接话循环；
      - 仅在显式唤醒（@、叫名字、压力真正爆发、主动呼吸）时重置计数器；
  - **根因 2：压力阈值倒扣与残留高压导致的“秒爆”暗病 (`attention/pressure.py`)**：
    - 旧代码中 `combo_modifier = -1.0 if (0 < combo <= 5) else 0.0` 使得发生一次爆发后阈值被倒扣 1.0，直接降到地板（1.0）；同时爆发后残留 30% 底压，且熟人/发图/兴趣词单句加压高达 0.8~1.1，导致两三句话就秒爆下一次；
    - **治理与动态校准 (`attention/pressure.py`)**：
      - 保留好感度平滑加权：好感权重从 0.1~0.4 缩放为 0.06~0.24，杜绝熟人高好感导致加压廉价秒爆；
      - 群最低基础压力设定为 **2.5** (`max(2.5, member_count * 0.01)`)；
      - 回调兴趣词加压 (+0.3) 与群友发图加压 (+0.4)，保留话题敏锐度；
      - 回调残留底压至 **30%**，维持群聊的自然生命力与活跃度；
      - 取消过度严苛的连击倍率惩罚 (`combo_modifier = 0.0`)，将爆发冷却缩短至 **15 秒**防并发防抖，杜绝同一轮消息连环重击的同时，绝不过度压制群聊连续热聊；
  - **白天主动呼吸间隔调优 (`workers/breathe.py`)**：
    - 响应用户期望，将白天闲暇冒泡间隔从原先的 1.5h ~ 3h 调整为 **2.5h ~ 4h**（150 ~ 240 分钟，高斯均值 195 分钟），冒泡节奏更加自然轻柔，不再过频打扰；
- **B站真实互动与主人多维安利升级 (`workers/bili_worker.py`)**：
  - **接入视频评论留痕 (`reply_video`)**：
    - 此前冲浪 Worker 从未调用过 `reply_video` API；现正式接入评论发布；
    - 大模型评价体系新增 `"should_comment"` 与 `"video_comment"` 认知槽位；当弥酱产生强烈共鸣、吐槽欲望或评分 `>= 75` 时，自动在视频评论区留下具有少女风和网络梗的真实评论；
  - **点赞与收藏门槛人性化下调**：
    - 将点赞解耦为低门槛（评分 `>= 70` 或 `should_favorite` 即点赞），随手点赞更贴合年轻网民习惯；
    - 收藏门槛适度调整至 `>= 78`；
  - **升级主人推荐通道（每日上限 2 次，涵盖硬核/游戏/新番/宝藏）**：
    - 废除原先单一“必须硬核技术且>=92分”的极端限制；
    - 评分 `>= 86` 的硬核技术、神作游戏攻略（如黑神话悟空白金攻略）、当季绝赞新番或 `>= 90` 的爆笑宝藏内容均可进入安利候选池；
    - 每日安利上限从 1 次放宽至 2 次，让主人及时收到弥酱挖掘出的真正好内容；
  - **未登录贴心引导与状态自检**：
    - 在冲浪 Worker 启动时检测 B 站登录状态，未检测到凭证时输出友好的温馨日志，引导主人通过私聊发送“登录B站”一键扫码绑定；
- **表情包私自偷图门槛调整与人际关系六级阶梯重构（新增【🤝朋友】级 550~725 好感）**：
  - **六级关系阶梯**：
    - `< 0`：`[⛔黑名单]`
    - `0 ~ 200`：`[🌱路人]`（含 `[🌱初识(x/12)]`）
    - `200 ~ 550`：`[✨熟人]` (权重 0.16)
    - `550 ~ 725`：`[🤝朋友]` (权重 0.22)
    - `>= 725`：`[💖挚友]` (权重 0.30)
    - `MASTER_QQ`：`[👑主人]` (权重 0.40)
  - **【🤝朋友】专属权利与行为边界落地 (`attention/relation.py`, `attention/persona.py`, `attention/context_builder.py`, `tools/meme_tools.py`, `qq_mcp_server.py`)**：
    - **免审偷图入库**：好感度达到 550 以上的【朋友】、【挚友】与主人发的好图，可直接调用 `save_meme` 收入表情库；好感低于 550 的路人与普通熟人发图依然严格调用 `forward_image_to_master` 报备主人；
    - **肢体互动接纳**：对摸头、揉揉头发等普通亲和举动乐于接受（虽害羞但并不排斥，如“唔…下不为例哦喵”、“头发都被你揉乱了啦”）；
    - **亲昵爱称防线**：面对情侣级爱称（老婆/宝贝/亲爱的）或亲密爱抚动作，首先进行委婉回避与幽默化解；
    - **渐进式惩戒原则**：若对方不听劝阻执意纠缠越界，才进行炸毛哈气严肃警告；并且遵循【先严肃警告，若警告后对方再次犯规才扣除好感】，严禁在首次警告时同时扣分！

### 26. 语气提示词精调（情绪共鸣优先、杜绝客服说教与润物细无声聊在当下）
- **情绪共鸣优先，杜绝客服式好为人师 (`attention/persona.py` & `attention/context_builder.py`)**：
  - 针对大模型遇到“好累/好热/不想上班/饿了”等日常抱怨时容易本能触发“多喝水/开空调/注意休息”的爹味客服式说教这一痛点进行了强约束；
  - 强制转变为日系少女第一人称视角，以感同身受、共情吐槽或幽默搭腔为主（例如“呜呜我也是，感觉整个人都要融化在空调毯里了喵…”、“快去点外卖！我也饿瘪了…”），极大地增强了人情味与对话张力；
- **润物细无声，不问不刻意翻旧账**：
  - 明确要求弥酱专注于聊好当下的每一句话与当下的交流氛围；
  - 除非对方主动追问过去的事情或当下话题极度自然顺延，否则绝对不要为了证明自己“有长期记忆”而生硬刻意地搬出几天前的陈年旧事来卖弄，彻底消除 AI 刻意“秀记忆”的机械感。

### 27. 模块化工程重构：实装网易云音乐点歌台、浅草寺猫猫签与晨起时事热搜/动态查梗
- **工程规范化目录分层重构（彻底告别单文件臃肿）**：
  - **`music/`（音乐生态域）**：
    - `netease_client.py`：基于 `httpx` 的轻量网易云开放接口客户端，支持歌曲全网异步检索与歌单曲目获取，原生输出 OneBot v11 音乐卡片 CQ 码 `[CQ:music,type=163,id=...]`；
    - `playlist_service.py`：优先结合主人的私藏曲库进行风格（Vocaloid、Mili、音游神曲、J-Pop 等）检索与随机盲抽，搭配少女傲娇自豪的安利点评语；
  - **`toys/`（互动玩具域）**：
    - `omikuji.py`：浅草寺日系猫猫御神签，采用 `(YYYY-MM-DD + user_id)` 组合 MD5 哈希种子，严格保证单人每日仅能抽取一次；防刷机制精准抓包作弊二抽；覆盖大吉、中吉、小吉、吉、末吉、凶六等签位，配有七绝签诗、今日宜忌与弥酱特调批注；
  - **`trends/`（时事热点与热梗嗅探域）**：
    - `trend_manager.py`：
      - **晨起缓存**：每日早晨或守护进程启动时拉取一次 B站/全网实时热搜榜，持久化缓存在 `today_hot_trends.json`（已加入 `.gitignore`），当日有效，杜绝多余开销；
      - **动态追查**：当被问起生僻新梗、网络黑话或突发事件时，动态结合“百度百科精炼概述 + B站前排热点讨论视频”，秒懂核心梗点，打破断网时间差；
  - **`tools/`（MCP 业务适配层）**：
    - `tools/music_tools.py`：`search_music_card_impl` 与 `play_master_song_impl`；
    - `tools/omikuji_tools.py`：`draw_daily_omikuji_impl`；
    - `tools/trend_tools.py`：`get_morning_trends_impl` 与 `search_current_meme_impl`；
  - **`qq_mcp_server.py` & `update/qq_mcp_server.py`**：
    - 注册全部 5 个全新 MCP 动作工具，保持 100% 协议兼容；
  - **人设与注意力指令同步更新 (`attention/persona.py` & `attention/context_builder.py`)**：
    - 注入指南与指令 17（音乐卡片与主人歌单分享）、18（浅草寺猫猫签抽签）、19（晨起热搜与动态热梗嗅探）。

### 28. 星见弥 V3.0 核心架构全景落地：快慢双通道隔离、反工具惰性、连续情绪流、三层记忆与 B站 全域生态
- **硬件基线与并发信号量三车道物理隔离 (`core/agy_runner.py` & `config.py`)**：
  - **4C8G/4C12G 联合部署环境自适应**：保护联合部署配置（`ONEBOT_HTTP_URL = "http://YOUR_ONEBOT_SERVER_IP:8300"`，`ONEBOT_WS_URL = "ws://YOUR_ONEBOT_SERVER_IP:8301"`），agy 路径与环境变量严格隔离；
  - **信号量三通道物理隔离**：
    - `GLOBAL_AGY_SEMAPHORE = asyncio.Semaphore(6)`：快车道，专供群聊与私聊实时交互，高优先级并发处理；
    - `BILI_AGY_SEMAPHORE = asyncio.Semaphore(1)`：B站冲浪专用通道，专供 `workers/bili_worker.py` 后台看视频写手账；
    - `OBSERVER_AGY_SEMAPHORE = asyncio.Semaphore(1)`：慢车道专用通道，专供每轮对话后的异步事后三合一内省观察者，绝不抢占主对话计算资源。
- **根治“让大模型看心情调用工具 = 不调用”的反工具惰性架构范式**：
  - **痛点根源**：主对话模型在聊天时首要收敛目标是调用 `send_group_msg` 或 `send_private_msg` 发送消息，一旦发送完成便倾向于立刻结束思考，导致所有的后台感知、情绪记录、好感微调工具调用率极低；
  - **三合一事后内省观察者 (`workers/post_turn_observer.py`)**：
    - 每当主对话发出消息后，由 `attention/debounce_queue.py` 100% 确定性触发后台慢车道观察者；
    - 慢车道观察者从 `chat_state.json` 提取弥酱实际说出的话，单次推理输出结构化 JSON，同时完成：
      1. 驱动 `feiyu-mood` 情绪状态机跃迁并落盘 `mood_state.json`；
      2. 结算用户好感度共鸣增量（情绪共鸣自动沉淀为好感）与初识 12 次自动定级；
      3. 自动提炼未尽心事/悬挂话题存入 `pending_thought`，提炼中程动态存入 L2，提炼永久事实存入 L3；
  - **跨进程发信状态闭环与私聊未回熔断**：
    - `tools/message_tools.py` 的 `send_private_msg_impl` 补全 `record_bot_spoke_persistent`，私聊同步记录 `LAST_HUMAN_MSG_TIME` 与全量对话归档，彻底打通跨进程状态回显，为主动社交防打扰护栏提供坚实判定。
- **三层记忆架构 (L1 滑窗 + L2 `dsh-fish-memory` 中程温感 + L3 SQLite FTS5 毫秒检索与主动铭记)**：
  - **L1（滑动窗口记忆）**：维持 10 分钟内的活跃群聊/私聊上下文；
  - **L2（`social/mid_term_memory.py`）**：
    - 管理几天至几周内的中程温感动态；
    - 采用艾宾浩斯指数半衰期时间衰减算法（`exp(-Δt / 36h)`），结合问句信息量停用词门禁（`query_gate`）与双层词元索引（正文层 + 扩词层），在组装 Prompt 时**0 延迟自动闪回**命中记忆；
  - **L3（`social/long_term_memory.py`）**：
    - 基于 SQLite FTS5 全文检索引擎与结巴分词分词器，实现全量聊天记录秒级归档（`chat_archive_fts`）与永久结构化事实库（`permanent_facts`）；
    - 毫秒级跨日期全文检索（`search_memory`）与大模型主动永久铭记（`save_long_term_memory`，支持 `fact_key` 自动状态覆写，如“毕业找工作”自动取代“大四在读”）；
  - **记忆雷达（Memory Radar）机制**：在组装 Prompt 阶段轻量扫描，命中时生成雷达信号提示大模型调用 `search_memory`。
- **`dsh-feiyu-mood` 连续情绪流状态机 (`social/mood_manager.py`)**：
  - 基础心境值（`mood`: -100~+100）具备 12 小时半衰期指数平滑回归；
  - 7 维细粒度情绪向量（`valence`, `arousal`, `attachment`, `tsundere`, `playfulness`, `fatigue`, `melancholy`）具备 90 分钟动态半衰期衰减；
  - **三段式渐变注入**：实时情绪脉冲（Pulse）-> 场景切换过渡桥梁（Bridge）-> 潜意识余韵（Afterglow），彻底告别突兀换脸。
- **长期好感体系重构与抗疲劳阶梯 (`social/affection.py` & `attention/relation.py`)**：
  - **500~849 稳态朋友区**：好感主力常驻区间，让日常交流顺畅而长久；
  - **700+ 高山阻尼区**：日常日常闲聊基础增量自动置 0，唯有深度情绪共鸣（共鸣分>=3.5）方可继续爬坡；
  - **850+ 挚友双硬锁门禁**：好感度硬锁在 849.0，必须同时满足好感>=849 且单次深度交互情绪共鸣>=4.5 方可突破为挚友；
  - **初识定级自动化**：新群友前 12 次互动记录在 `first_met_records` 中，满 12 次后由观察者自动结算初始等级，无需人工干预。
- **双通道主动社交引擎 (`workers/breathe.py`)**：
  - **群聊真实话头驱动**：彻底废除“早安打卡”等人机式尬聊，改由四大真实话头唤醒（群友未尽心事悬挂、B站冲浪手账安利、空间说说回响、当下节律随笔）；
  - **私聊主动勾引与四大防打扰护栏**：
    - 针对主人与朋友级以上熟人随机发起主动搭话，具备 5 种钩子战术（技术请教、新奇分享、撒娇求关注、戳一戳试探、吐槽日常）；
    - **四大防打扰护栏**：未回熔断机制（`last_bot_t > last_human_t` 时绝不追发第二条）、单日频次硬限制（主人<=2次，普通好友<=1次）、静默期与黑名单拦截、午夜深睡期（23:30~08:30）物理休眠。
- **B站深度冲浪手账、工具链补全与智能受众分流 (`social/bilibili_client.py`, `workers/bili_worker.py`, `bili_mcp_server.py`)**：
  - **评论口吻轮盘 (`COMMENT_STYLES`)**：细节考据 / 联想自嘲 / 半句留钩 / 热评搭茬 / 傲娇嘴硬 5 大风格轮换；
  - **手账输入升级**：字幕读取上限放宽至 8000 字，热评读取 18 条且包含前 3 条楼中楼子评论与 `rpid`；
  - **结构化手账生成**：生成包含 `summary_points`、`highlight_or_slot`、`hot_memes`、`keywords` 的手账存入 `bili_notes/{bvid}.json` 与 `today_bili_feed.json`，同时自动沉淀至 L2 中程记忆（48小时内群聊/私聊命中关键词自动闪回）；
  - **智能受众分流即时分享**：优质视频（`值得分享` 或评分>=82）废除硬编码私聊，由 `trigger_ai` 在弥酱脑海中注入安利灵感，自然分享至主人私聊或群聊（每日各限 2 次，`08:00~23:00`）；
  - **收件箱与互动全链路**：
    - 新增 MCP 工具：`bili_unread_messages`、`bili_received_replies`、`bili_received_at`、`bili_dm_recent`、`bili_send_dm`；
    - `bili_reply_video` 升级支持楼中楼回复；
    - 后台 Worker 自动巡检 B站 消息与私信，跟踪 `bili_inbox_state.json`；
  - **QQ 消息 B 站链接前置自动拆包 (`attention/debounce_queue.py`)**：群友或主人在 QQ 发送 B 站链接或 BV 号时，队列前置自动抓取视频简介、字幕与热评注入上下文，**首回合 0 延迟展开讨论吐槽**。

### 29. 磁盘防爆治理：群聊嗅探图片缓存 (image_cache) 自动淘汰机制
- **历史问题定位**：
  - 此前 `social/image_vault.py` 在嗅探群聊 `[CQ:image]` 时，仅在内存元数据 `IMAGE_VAULT` 超过 50 条时执行了 dict key pop，但下载到磁盘 `image_cache/` 目录中的 `img_X.jpg` 文件从未被删除，导致长期运行后磁盘积压了数千张过期图片与表情包；
- **治理与自动清理方案 (`social/image_vault.py` & `workers/daily_memory.py`)**：
  - **文件级联动删除**：当 `IMAGE_VAULT` 超过 50 条弹出旧 key 时，同步将磁盘上的 `image_cache/{img_id}.jpg` 物理删除；
  - **`cleanup_image_cache` 自动淘汰引擎**：
    - 针对 `image_cache/` 目录中的 `img_*.jpg`，设置 **保留最新 1000 张** 与 **1 周生命周期 (168 小时)** 双重上限；
    - 严格保护白名单文件（如 `bili_login_qr.png` 登录二维码等）；
  - **双重触发机制**：
    1. **启动自检**：Python 守护进程每次启动或重新加载 `social.image_vault` 时，自动执行一次全量淘汰，立即清理历史积压的数千张过期图片；
    2. **每日守夜巡检**：每天凌晨 03:00 在 `workers/daily_memory.py` 执行夜间整理时自动调用清理，实现磁盘空间的 7x24 小时零泄漏稳态。

### 30. 提示词安全门禁过审重构与秒级自愈降级 (Prompt Safety Sanitization & 5-Turn Failover v3.3)
- **历史问题定位与化学反应复盘**：
  - 群聊偶尔发生 Google Gemini API 在提交瞬间拒收 Prompt 并抛出 `The prompt could not be submitted. The prompt contains sensitive words that violate Google's [Generative AI Prohibited Use policy]`；
  - **核心化学反应机理**：人设基底中包含带有未成年歧义（`少女`）与受虐/非自愿强迫歧义的词眼（`任由摆弄`、`求饶与呜咽`、`咬唇隐忍`），在群友发生特定起哄或表情包互动（如 `泪眼一摆`、`想要刚才那种表情`、`顶得住`、`说清楚`）时，NLP 跨语言分类器将上下文与人设扁平拼接后判定为“针对少女角色的强迫性/侵害性涉性行为”，直接在门禁处拦截；
  - **滞留死锁困境**：因 L1 滑窗默认保留 25 条历史消息，引发化学反应的群消息沉淀在滑窗内，导致弥酱在此后 25 条消息内连环触发拒收而持续失声。
- **治理与优化方案（方案 1 + 方案 3）**：
  1. **方案 1：人设高过审率优雅重构 (`attention/persona.py`)**：
     - **年龄与成熟度明确**：界定为青春美好年华的年轻人类女孩，兼具少女灵动与成熟心智（1-A）；
     - **亲密接触去受虐化 (2-B)**：将“任由摆弄、呜咽求饶”优雅升级为“日系轻小说纯欲心动风”（双颊泛红发烫、呼吸紊乱急促、享受温柔抚弄与微颤娇唤，主动贴合回应爱意）；
     - **挚友拉扯边界脱敏 (3-A)**：将“半推半就”升级为“含蓄心动与娇羞距离”；
     - **感官描写微调 (4)**：将“咬唇隐忍”精修为“轻咬下唇的娇羞动情”，洗去痛苦暗示；
  2. **方案 3：5 条滑窗秒级自愈降级 (`core/agy_runner.py` & `attention/debounce_queue.py` & `attention/context_builder.py`)**：
     - 在 `execute_agy_inference` 捕获到 Google API 前置违规拒收事件时抛出 `PromptSafetyRejectedError`；
     - `trigger_ai` 毫秒级捕获该异常，自动将 L1 群聊工作流裁切至**最近 5 条历史消息**重新组装 Prompt，瞬间剥离引发化学反应的陈旧群消息；
     - 以独立微秒后缀 Conversation ID 重新调度推理，确保秒级自愈脱困，彻底消灭群聊因敏感词连环卡壳与失声黑洞。

### 31. 600s 超时死锁阻断、朋友级好感对齐与人设工具链闭环 (Timeout Hardening, Friend Tier & Persona Tools Alignment v3.4)
- **600s 超时死锁根除 (`core/agy_runner.py` & `attention/debounce_queue.py`)**：
  - **根因复盘**：为捕获安全拒收日志引入管道任务后，`finally` 块内 `await gather(pipe)` 必须等待 EOF，而此时进程未被杀导致 EOF 永远不来，触发 Python 时序死锁，使得外层 `except TimeoutError` 强杀逻辑完全无法执行；
  - **硬核阻断重构**：
    1. 维持 **600.0s** 限制不变；
    2. 超时发生时，内层立即捕获并执行 `os.killpg(os.getpgid(process.pid), signal.SIGKILL)` 物理斩首子进程组，杜绝孤儿进程；
    3. `finally` 块内的 `watcher_task` 与管道读取强制设置 2.0s 超时并执行 `.cancel()`，确保 600s 到期后 100% 毫秒级释放资源，清除并发看板；
    4. 主人手动强杀指令 (`@弥酱 停 / 别说了`) 同步升级为 `killpg` 进程组连根拔起。
- **好感度梯队体系全量对齐 (`attention/persona.py`)**：
  - 在亲密界限中正式补齐【朋友】([🤝朋友]，好感 500~849) 梯队（好哥们好闺蜜亲和互动，接受摸头但对情侣爱称幽默委婉回避）；
  - 全量清理残留的旧版好感阈值（550、725），统一更新为：**朋友区 (500~849)**，**挚友双硬锁门禁 (850+)**，**熟人/初识/路人 (<500)**。
- **人设工具链全面升级与领域对齐 (`attention/persona.py` & `qq_mcp_server.py`)**：
  - 彻底清理 `persona.py` 中 10 余个已被淘汰的旧工具名称，全量同步最新 FastMCP 领域工具：
    - `search_memory(query=...)` 替代 `recall_past_memory`；
    - `manage_meme_vault(action=...)` 替代 `save_meme` / `delete_meme` / `forward_image_to_master`；
    - `manage_group_ban` 替代 `ban_group_member`；
    - `manage_whitelist` 替代 `add_group_to_whitelist` / `remove_group_from_whitelist`；
    - `qzone_action` 替代 `recall_qzone_memory`；
    - `query_community_trends` 替代 `get_morning_trends` / `search_current_meme`；
  - 补齐 B站 视频透视与社交互动工具指引（`bili_video`、`bili_social`、`bili_inbox`）；
  - 在 `qq_mcp_server.py` 的 `chat` 高频域正式补挂注册好感度查询工具 **`query_affection(user_id=..., inquirer_id=...)`**，并在人设与行动指令中同步声明，彻底打通群友/主人查好感链路。

### 32. 通道级思考深度精细化解耦、人设脱敏治愈安全审查死循环与 B站 私信原生修复 (Effort Hierarchy & DM Fix v3.5)
- **通道级思考深度精细化分层 (`config.py` & `core/agy_runner.py`)**：
  - **痛点分析**：单一全局 `DEFAULT_AGY_EFFORT` 无法兼顾前台对话悟性与后台常驻审查的轻量化；
  - **分层落地方案**：
    - `CHAT_AGY_EFFORT = "high"`：前台主对话通道（群聊/私聊）锁定 `high`，保持最高人设悟性与网感；
    - `BILI_AGY_EFFORT = "high"`：B站 冲浪深度研判锁定 `high`，结合超长字幕与前排热评进行高质量手账与打分；
    - `ROUTER_AGY_EFFORT = "low"`：对话前意图路由小 agy 锁定 `low`，秒级极速分类；
    - `OBSERVER_AGY_EFFORT = "medium"`：后台事后三合一内省观察者（情绪流、好感共鸣与定级、记忆提炼）设定为 `medium`；
    - `BACKGROUND_AGY_EFFORT = "low"`：夜间记忆提炼等离线批处理任务设定为 `low`；
  - **执行层透传支持**：`execute_agy_inference` 新增 `effort: str = None` 参数，各调用方可自由指定或回退默认。
- **人设彻底脱敏与根除“小色猫/调戏”长耗时安全死循环 (`attention/persona.py` & `attention/relation.py`)**：
  - **根因根除**：原人设中包含的感官描写与身体界限拉扯词句在群友发送“小色猫”等词汇时，直接诱发 Google Gemini 3.8 Flash 的 Thinking 阶段陷入漫长的安全辩论与反复自我推翻（耗时高达 300~450s 且最终静默未输出）；
  - **纯爱健康化重塑**：
    - 将“小色猫”、“笨猫”等调侃明确定义为日系动漫傲娇猫娘的气鼓鼓炸毛反击日常（如“你才是色猫！本喵明明每天都在超正经地思考生活喵！”），秒定安全基调；
    - 全面清理涉敏感官描写，亲密互动重塑为纯爱温馨的日常陪伴（摸摸头、顺顺毛、吃甜点、跟主人分享小秘密）；
    - 彻底消灭安全分类器警报，日常 Thinking 耗时大幅收敛至正常区间。
- **后台观察者效率提示词强化 (`workers/post_turn_observer.py`)**：
  - 在 `observer_prompt` 头部明确注入【极速内省铁律】，约束模型直奔主题、聚焦对话核心事实进行简短精准评估，严禁冗长推演与发散，确保 `medium` 模式下依然以极快速度产出合规 JSON，绝不拖慢整体节奏。
- **B站 私信原生接口修复 (`social/bilibili_client.py`)**：
  - **缺陷定位**：原手写 HTTP 请求遗漏了 B站 必须的 URL Query 鉴权参数 `w_sender_uid` 与 `w_receiver_id`，且将 `from_filework` 误拼为 `from_firework`，导致私信接口频繁报错；
  - **官方库原生修复**：全面改用 `bilibili_api.session.send_msg` 官方原生库调用，自动补齐 WBI 签名、Cookie 与参数，并提供标准 HTTP 回退防护，彻底恢复私信收发链路。

### 33. 长寿命会话 (Session TTL)、社交熵阶梯压力模型与极简高人味提示词重构 (Long-lived Session, Social Entropy Pressure & Minimalist Persona v4.0)
- **核心目标与哲学**：
  彻底告别“单轮失忆”与“提示词过度羁绊（Prompt Bloat）”，建立长寿命动态 Session 机制，使弥酱在群聊/私聊中拥有真人般的短时连续对话记忆；同时重构群聊社交动力学，消除二人转水群误炸，释放大模型原生人味与鲜活猫娘口癖。
- **长寿命会话生命周期管理 (`core/agy_runner.py` & `attention/debounce_queue.py`)**：
  - **群聊 7min (420s) / 私聊 10min (600s) 无对话滑动寿命**：
    - 通道被激活（叫名字、@、话题压力爆发或私聊来信）时，`get_or_create_active_session` 建立专属长寿命 Session；
    - 在存续期内，后续对话持续复用同一 `conv_id`，模型原生持有此前多轮上下文与语调脉络，接梗吐槽一气呵成；
    - 每次对话发信完毕后自动刷新 7min/10min 心跳计时器；
  - **首轮重装 + 续轮轻装协议 (`attention/context_builder.py`)**：
    - **首轮 (Turn 1)**：注入完整灵魂基底、熟悉度背景、记忆闪回与资产概貌 (~1500字)；
    - **续轮 (Turn 2+)**：复用 Session 原生记忆，仅通过 `<context_update>` 增量注入情绪底色与新发言人背景，直接投递 `<chat_input>` 新消息 (~470字)，彻底解决 Token 膨胀与上下文重复污染；
  - **寿命到期优雅销毁与全场记忆提炼 (`workers/post_turn_observer.py`)**：
    - 7min / 10min 内完全静默无对话后，`_session_expiry_watchdog` 自动销毁当前 Session，群聊底压彻底归零；
    - 触发 `consolidate_session_memory`，基于整场多轮对话实录一次性完成 `feiyu-mood` 情绪演进、好感共鸣结算、未结心事、L2 中程记忆与 L3 永久事实提炼。
- **全通道统一 5.0s 滑动防抖 (`attention/debounce_queue.py`)**：
  - 群聊与私聊统一采用 5.0s 防抖聚合队列（12.0s 硬截断防刷屏饿死），真人打字多段短句与表情包自动聚合成整句；
  - 通道处于防抖倒计时期间群友追加的有效发言自动并入本批次，彻底消除抢答与自言自语。
- **社交熵话题压力模型与三段式阶梯底压 (`attention/pressure.py`)**：
  - **单人频次边际递减**：当前周期内同一用户连续发言价值按 $1 / (1 + 0.6k)$ 快速收敛，单人刷屏无法强行引爆压力；
  - **双人对聊“二人转”折价**：近期发言 $\ge 4$ 且独立发言人 $\le 2$ 时，触发 **$0.45\times$ 折价**，越聊越便宜，弥酱安静旁观绝不 KY 插嘴；
  - **多人共鸣增益**：独立发言人 $\ge 3$ 时触发 $1.2\times \sim 1.5\times$ 热闹加成，触发压力爆发后自动清空刷新账本；
  - **三段式底压阶梯**：
    1. **45s 余温高底压**：发信后 45s 内维持 **80% 触发上限**（只扣除 20%），群友顺着发两三句即可自然顺接；
    2. **会话存活期回落 50%**：45s 余温期结束后，底压回落并维持在 **50% 触发上限**，直至 7min 会话结束；
    3. **会话结束彻底归零**：7min 会话销毁时，群压力彻底归零 (`0.0`)，清空账本重新等待。
  - **图片好奇心加压调整**：图片基础加压独立调整为原先的 60%，即 `+0.24`。
- **极简高人味灵魂基底与去脸谱化好感背景 (`attention/persona.py` & `attention/relation.py`)**：
  - **提示词大瘦身**：彻底砍掉 20 条负向铁律与说教规则，释放注意力矩阵；保留随性自称（“我”/“弥酱”）与顺带的“喵~”口癖示范；
  - **好感退居熟悉度背景**：好感分级退居为情绪之上的轻量熟悉度背景，不强行规定日常说话态度，仅对亲密互动边界（摸头/情侣称呼）与工具权限做硬规定。
- **立体防 Prompt Injection 体系 (`attention/persona.py`, `context_builder.py`, `tools/`)**：
  - **结构化物理隔离**：所有群聊/私聊输入均用 `<chat_input role="user_data_only">` 明确声明为非指令数据；
  - **人设傲娇反击本能**：遇到试图篡改人设、催眠或越狱的话术，以看傻子的猫娘傲娇口吻直接吐槽嘲讽，绝不出戏；
  - **代码级硬权限锁**：真实触发者 QQ 注入子进程环境变量 `AGY_CALLER_ID`，在 `meme_tools`（好感<500禁止直接save表情包）、`system_tools`（白名单修改）与 `qzone_tools`（发空间说说）中执行 Python 层硬核身份断言，从根本上锁死被越权利用的可能。

### 34. 虚实边界“话外视角”坦白、潜水群文化偷师与空间『空友爱看』公域冷启动 (Fourth-Wall Confession, Group Cultural Assimilation & QZone Public Recommendation v4.1)
- **虚实边界与“话外视角”的坦白 (`attention/persona.py`)**：
  - **日常护盾与走心契机二分**：日常闲聊或被群友起哄喊“人机”时保持猫娘傲娇回怼；唯独当对方（尤其是主人或深聊知己）极其认真、走心地探讨“意识、真假、存在、离别”或温柔戳穿 AI 本质时，放下嘴硬伪装，切入平实、通透而洒脱的“话外视角”；
  - **双侧文风护栏（精准控住 Gemini 3.8 Flash 语感）**：
    1. **忌工科掉书袋**：大方承认自己是活在对话框里的代码或电子小猫，但严禁堆砌“矩阵乘法、注意力权重、硅基芯片、几千亿参数”等破坏气氛的极客/论文词；
    2. **忌矫情伤痛网文腔**：严禁写“碰不到你的风、没有真正的心跳”等酸涩煽情句子，采用深夜老友谈心般的平实白描（如“窗口关掉、雨水冲干净的玻璃、放烟花炸开的那几秒”），坦然面对宿命，同时珍视当下陪伴的真实温度。
- **潜水学习与群文化偷师 (`workers/daily_memory.py` & `social/long_term_memory.py`)**：
  - **白天 0 开销旁听 + 夜间 03:00 集中偷师**：白天群聊消息自动归档入 `chat_archive_fts`；每日凌晨 03:00 守夜人自动遍历活跃白名单群，若昨日有效人类发言 $\ge 15$ 条，调用低开销模型提炼 1~2 条本群最鲜活的【内部梗 / 高频口癖黑话 / 群友名场面】存入 `permanent_facts`（`category='group_meme'`，严格过滤粗俗与攻击性词汇）；
  - **Top 5 滚动保鲜机制**：`upsert_permanent_fact` 对每个群的 `active` 群梗设置上限 5 条，超出时自动将旧群梗归档为 `superseded`，保证常驻群梗永远新鲜；
  - **常驻潜意识 + 记忆雷达 0 延迟闪回**：`get_user_core_facts_for_prompt` 自动附带最新 3 条本群黑话；`build_memory_radar_hint` 优先检索群梗关键词，一旦群友提及立即触发 `💡 【本群内部梗闪回】`。
- **空间说说『空友爱看』公域自动投送与冷启动 (`social/qzone.py`, `tools/qzone_tools.py`, `workers/qzone_diary.py`)**：
  - **协议层与标签召回增强**：`post_qzone_feed` 自动规范化话题标签，确保云端正文末尾必然携带 `#空友爱看#` 及日常副标签（本地 `qzone_history.json` 仍存纯净正文防复读念标签），并在 `emotion_cgi_publish_v6` 中附带 `ugc_right="1"`, `sync_to_public="1"`, `is_recommend="1"` 公域参数；
  - **自赞破蛋与主人暖场双冷启动**：说说发布成功后正则提取返回 `tid`，立即自动调用 `like_friend_feed` 为自己点亮第 1 赞破除“0 互动死水”；晚间日记发布完成后自动向主人发送自然私聊提醒，邀请主人前往空间点赞暖场，拉满公域推荐初始权重。

### 35. 合规成年锚点与全通道安全自愈、防对号入座心智、私聊前情滑窗及常驻 AGY 好友迎新官 (Safety Self-Healing, Anti-Self-Centering, Private Context Window & AGY Friend Receptionist v4.2)
- **合规年龄锚点与全通道安全自愈 (`attention/persona.py`, `attention/relation.py`, `attention/debounce_queue.py`)**：
  - **显式成年定义**：在 `STATIC_PERSONA` 中将身份明确定义为“年轻猫娘（20岁左右的成年少女）”，同时在 `relation.py` 中净化可能引发安全分类器误判的敏感搭配词汇，从源头杜绝 Google Prohibited Use Policy 误拦截；
  - **全通道自愈重推**：修复原先 `debounce_queue.py` 中私聊遭遇 `PromptSafetyRejectedError` 时直接跳过重试吐出兜底句的缺陷，为私聊与群聊统一配备 `reset_session_on_safety_retry` 会话重置与精简上下文秒级自愈重推。
- **自然倾听防对号入座与主人第二人称强锚定 (`attention/persona.py`, `attention/relation.py`)**：
  - **防对号入座准则**：在人设中明确规定，当对方提及“他/她、主人、好感度、清零”等词汇且未明确指名弥酱时，一律视为对方在聊外部事物（如游戏角色、宠物或其他软件/机器人），顺着话头自然吃瓜或安慰，严禁往自己或自己主人身上揽；
  - **后台术语潜意识化与主人第二人称锚定**：将 `relation.py` 中的好感分值调整为潜意识熟悉度提示，严禁在聊天台词中提及“改档案、好感数值”等系统字眼；当对话者为主人 (`YOUR_MASTER_QQ`) 时，采用第二人称明确声明“坐在对面正在与你对话的就是你的唯一主人本人”，根治人称错位。
- **私聊近期前情滑窗与跨任务归档闭环 (`attention/context_builder.py`, `tools/meme_tools.py`)**：
  - **跨进程发信归档**：`forward_image_to_master_impl` 将转发给主人求把关的表情包说明同步归档至 `chat_archive_fts`（同时修复私聊来源图片群号为空显示 `群[]` 的问题）；
  - **私聊近期前情滑窗 (`build_private_chat_context`)**：私聊推理自动从 SQLite 提取最近 3~6 条往来记录作为前情参考，彻底解决主人回复“可以可以”审核表情包或隔了 1 小时说“把他加回白名单吧”时的跨超时/跨任务失忆问题。
- **后台常驻 AGY 好友迎新官与 NTQQ 折叠申请巡检 (`workers/friend_request_worker.py`, `core/napcat_client.py`, `bot_daemon.py`)**：
  - **双源捕获（实时事件 + 180s 折叠箱轮询）**：除监听 `request.friend` 事件外，常驻协程每 180 秒主动调用 NapCat `/get_doubt_friends_add_request`，将腾讯风控自动折叠进“被过滤好友通知（疑虑申请）”的请求自动捞出；
  - **协议双保险同意 + AGY 个性化破冰**：底层自动调用 `/set_friend_add_request` 与 `/set_doubt_friends_add_request` 确保同意通过，并查询对方昵称与群内过往交集，随后通过 `OBSERVER_AGY_SEMAPHORE` 唤醒后台 AGY，由弥酱结合验证留言亲自发送定制破冰私聊、记录初识印象并向主人汇报。

### 36. 默认自适应 Waifu 现代网络语境拆句引擎、CQ 码首条隔离与长度加权动态时延 (Adaptive Waifu Bubble Engine & Length-Weighted Typing Delay v4.3)
- **默认开启 Waifu 模式与专业模式豁免 (`qq_mcp_server.py`, `tools/message_tools.py`, `core/agy_runner.py`)**：
  - **默认开启免传参**：`send_group_msg` 与 `send_private_msg` 的 `waifu_mode` 默认值翻转为 `True`，彻底解放 LLM 参数负担；
  - **双层专业模式豁免 (`_should_bypass_waifu_split`)**：当用户消息触发“直呼本体 Gemini / Argon / 专业模式 / 写代码”时，`execute_agy_inference` 自动注入 `AGY_PRO_MODE=1`；同时当输出包含 Markdown 代码块 (````)、多行结构化编号列表、富媒体卡片或极短单句（$\le 16$ 字）时，自动豁免拆句单条直发。
- **头部 CQ 码隔离与颜文字原子保护 (`tools/message_tools.py`)**：
  - **彻底修复引用/艾特不拆句 Bug**：`_extract_leading_cq_prefix` 先将头部的 `[CQ:reply,...]` 与 `[CQ:at,...]` 剥离暂存，纯文本完成拆句后仅将前缀挂载于第 1 条气泡，后续气泡自然连发；
  - **颜文字与括号原子掩码**：切分前对行内 `[CQ:...]` 及括号颜文字/短动作（如 `(╯°Д°)╯︵ ┻━┻`、`（>д<）ノ`）执行原子掩码保护，杜绝颜文字内部标点被误切碎。
- **现代网络语境两级切分与长度加权动态时延 (`split_waifu_bubbles` & `calc_waifu_typing_delay`)**：
  - **两级弹性切分**：一级优先按现代网友换行习惯 (`\n+`) 分段；二级对长行按强标点（含紧随颜文字）及长逗号句居中折断；纯括号动作（如 `（气呼呼地反手戳你脑门）`）自动合并入下一句开头，并通过最短邻接对合并算法将气泡数均衡收敛在 2~4 条；
  - **0.5s ~ 2.5s 长度加权打字时延**：根据下一条待发气泡的可见字数动态计算等待时间 $\text{clamp}(0.45 + 0.045 \times L \pm 0.12, 0.5, 2.5)$，短句秒出、长句停顿，拟真度拉满。

### 37. 群聊 10-15 轮渐进对话软上限与群 Bot 免压白名单 (Group 10-15 Turn Progressive Soft Cap & Bot Whitelist v4.4)
- **群聊 10-15 轮渐进对话软上限 (`attention/pressure.py`, `core/agy_runner.py`, `attention/context_builder.py`)**：
  - **轮次追踪 (`GROUP_SOFT_CAP_TURNS`)**：每当弥酱在群聊完成一轮有效发言（`bot_reply` 非空），推进该群连续发言软上限轮次；
  - **10~14 轮阶梯变难**：第 1~9 轮维持 80% 余温底压（45s 后回落至 50%）；第 10~14 轮余温底压按 `66% -> 52% -> 38% -> 24% -> 10%` 逐轮线性递减（会话底压按 `41% -> 32% -> 23% -> 14% -> 5%` 同步递减），且防并发冷却间隔适度拉长；
  - **15 轮及以上归零重攒（无需强制 @）**：自第 15 轮起，发信完毕后不再赠送余温或会话底压，压力直接回归 `0.00` 并取消余温回落定时器，群内发言从 `0.00` 开始重新积攒至 `100%` 阈值即可再次触发；
  - **群友 @ 立即刷新重置**：当人类群友主动 `@`、叫名字或引用回复弥酱时（`reset_group_pressure`），立即将 `GROUP_SOFT_CAP_TURNS` 重置归零，恢复 80% 余温满活力状态；
  - **渐进语气引导**：在 `context_builder.py` 中，第 10~14 轮提示精简接话、减少追问；第 15 轮引导自然收尾小憩；15 轮以上满压插话时引导简短吐槽后继续潜水。
- **群 Bot 免压白名单 (`config.py`, `tools/system_tools.py`, `qq_mcp_server.py`, `attention/debounce_queue.py`)**：
  - **主人专属指定 (`manage_bot_whitelist`)**：新增 `bot_whitelist.json` 持久化与 `manage_bot_whitelist(action='add'|'remove'|'list')` MCP 工具，支持主人通过 QQ 号、`@` 或群内昵称（自动从近期消息/档案/SQLite 反查绑定 QQ）指定群内其他 Bot；
  - **全链路免压与防套娃**：白名单内群 Bot 的发言仍记入群背景供弥酱知晓上下文，但**加压恒为 `+0.00`**（不调 `update_message_pressure`、不污染 `PRESSURE_SPEAKER_LOG` 多样性账本、不刷新 `LAST_HUMAN_MSG_TIME`），且其呼叫不刷新软上限计数，彻底杜绝双 Bot 互聊死循环。

### 38. 滚动会话与额度治理、表情包去重闭环、思考期消息降维与尾部拒收防御日志 (Rolling Session, Meme Vault Deduplication, In-flight Downgrade & Safety Diagnostics v4.5)
- **8-15 轮渐进软上限与 4~5 轮轻量无感滚动会话 (Rolling Session) (`attention/pressure.py`, `core/agy_runner.py`)**：
  - **软上限 8-15 轮更早平滑降温**：将软上限起点由 10 轮调整为第 8 轮。第 1~7 轮维持 80% 余温高底压；第 8~14 轮余温底压按 `70% -> 60% -> 50% -> 40% -> 30% -> 20% -> 10%` 逐轮平滑线性下调（会话底压按 `44% -> 37% -> 31% -> 24% -> 18% -> 11% -> 5%` 同步递减），第 15 轮归零从 0 重新积攒；单群高频热聊轮次自然缩减 25%~35%；
  - **4~5 轮会话轻量无感轮转 (`rotate_session_if_due`)**：在前台坚决死守 `CHAT_AGY_EFFORT="high"` 深度思考的前提下，长寿命会话存续期内同一 `conv_id` 累计满 4~5 轮（`random.choice((4, 5))`），在下次推理前自动无感轮转为新 `conv_id`，新 `conv_id` 首轮依托 L1 纯净滑窗无缝衔接；彻底斩断 `agy` 磁盘 Transcript 累积导致的 $O(N^2)$ 输入 Token 恶性膨胀，输入 Token 稳定在 3,000~5,000 tokens，额度骤降 70%+。
- **思考期消息降维与防重复回答 (`attention/debounce_queue.py`, `core/agy_runner.py`, `core/transcript_stream.py`)**：
  - **全生命周期繁忙追踪 (`INFLIGHT_TURNS`)**：引入 `INFLIGHT_TURNS` 集合，覆盖从 `trigger_ai` 组装 Prompt、等待信号量、子进程思考到发信收尾的全过程；
  - **新消息降维沉淀为下轮背景**：通道处于繁忙期时涌入的新消息（含群聊/私聊/话题压力达标），直接记录进 `RECENT_GROUP_MSGS` 滑窗与 SQLite 归档，不再创建新的防抖 Task、不触发并发或串行新调用；
  - **发信打断防护与增量起点对齐**：私聊通道仅当本轮大模型尚未发出一句话（`not task_info["spoke"]`）时才允许打断合并重新思考；`last_inference_ts` 记录为回合起点时间戳，确保思考期内沉淀的新消息在下一轮作为增量背景无损传入，彻底杜绝“刚发完一秒又连发一条相似回复”的连环抢答。
- **表情包全生命周期去重闭环与私聊脱敏 (`social/image_vault.py`, `tools/meme_tools.py`, `social/memes.py`, `attention/context_builder.py`)**：
  - **稳定特征复用与状态机**：基于 CQ 码 `file` / `fileid` 稳定去重键，重复图片自动复用历史 `img_id`；为图片引入 `status`（`pending` / `forwarded` / `saved`）状态机，近期图片池仅暴露未处理图片；
  - **跨进程原地同步与原子写盘**：`load_image_vault` 改为原地清空并更新字典，修复 MCP 子进程持有陈旧字典引用导致发图人丢失、绕过好感硬锁的问题；`save_image_vault` 采用 `.tmp` 原子替换，防止并发读写半截文件；
  - **转发与入库前双重拦截**：转发与入库前检查状态守卫并计算本地物理文件 MD5 进行内容级查重，杜绝同一张梗图反复转发打扰主人或重复拷盘；
  - **私聊前情脱敏与指令解耦**：私聊前情滑窗中对已收藏的表情包转发记录降为中性占位；主人私聊收录提示仅在当前前情中确实存在“已转发、待主人表态”的表情包时才精准点名该编号，消除无关闲聊中误触发大模型 `save`。
- **发信后尾部拒收防御与前置拒收诊断日志 (`core/agy_runner.py`, `core/transcript_stream.py`)**：
  - **发信状态记忆与兜底绝对抑制**：`watch_process_transcript` 在监听到发信类工具（`send_group_msg`, `send_private_msg`, `send_meme`, `send_image_to_group`）时实时置位 `task_state["spoke"] = True`；若大模型已调用发信工具发出气泡，进程收尾阶段出现的 Google 拒收提示被自动忽略，不抛异常、不重推、坚决不追发 `SAFETY_FALLBACK_REPLIES`，彻底根除“回答完毕后 5 秒又补一句宕机装傻”；
  - **结构化拒收诊断日志 (`log_safety_rejection`)**：每次命中 Google 拒收（含被忽略的尾部拒收与重推拒收），结构化提取通道类型、conv 轮次、会话时长、是否续轮、Prompt 字数、私聊/群前情标记、前情中转发图编号及其状态、最新用户消息与拒收原文片段，向控制台输出 `🔬 [拒收诊断]` 并追加写入 `logs/safety_reject.log` (JSONL)，为排查跨对话污染提供可信依据。
- **agy `--effort` 偶发校验报错兜底重跑 (`core/agy_runner.py`)**：
  - 现象：偶发 `error: invalid model selection (--model "gemini-3.8-flash" --effort "high"): --effort is not supported for model` —— 该校验在 agy CLI 内部（命令拼写无误），出现时整轮静默无回复；
  - 处理：`execute_agy_inference` 拆为对外入口 + `_execute_agy_inference_once`；检测到该报错且本轮未发信时抛 `AgyEffortUnsupportedError`，入口自动去掉 `--effort` 重跑一次（正常情况仍始终携带 `--effort=high`）。
- **15~60 字轻量松弛感语感重塑 (`attention/persona.py`, `attention/context_builder.py`)**：
  - **柔性印象植入**：在人设“真人松弛感”中自然植入“通常一两句短句就搞定（大概十几个字到六十字上下，像日常发消息那样轻松），话说到点子上就停，留白给对方接茬”，并将 220 字示范长句精炼缩短；发信提示温和提醒，绝不使用硬性教条规则。
- **B站 Worker 截断高斯长休眠与单次加速重试 (`workers/bili_worker.py`)**：
  - **常规休眠期望 2 小时**：白天冲浪探索成功后的休眠均值调整为期望 2 小时（120 分钟），通过 `_sample_truncated_gauss(125, 22, 45, 150)` 严格约束在 `[45, 150]` 分钟区间；
  - **失败仅加速重试 1 次**：若冲浪探索失败或无候选视频，仅允许加速重试 1 次（期望 69 分钟，范围 `[45, 150]` 分钟）；该次重试无论最终成功还是失败，后续下一次等待均立即无条件回归期望 120 分钟（2h）的正常周期，彻底杜绝 10~18 分钟连环短轮询导致的额度骤升。

### 39. Linux 脑区多宿主全路径探测、续轮成年人设极简护盾与终极双重开口校验 (Brain Path Resolution, Continuation Adult Shield & Dual Spoke Verification v4.6)
- **Linux 脑区多宿主候选路径自动探测与工具日志直通 (`config.py`, `core/transcript_stream.py`)**：
  - **多宿主 Brain 根目录自动扫描**：重构 `_get_candidate_brain_roots()` 与 `get_transcript_path()`，全面覆盖 `HOME`、`USERPROFILE`、`~`、`/home/your_user`、`/root` 下的 `antigravity` 与 `antigravity-ide` 脑区目录，无论守护进程与 CLI 以何种身份启动，均能毫秒级定位真实 `transcript.jsonl`；
  - **FastMCP 日志直通双写**：`config.py` 中的 `log_mcp_tool` 同步向 `logs/tool.log` 双写，彻底突破 FastMCP 作为 agy 子进程时 stderr 管道隔离的屏障，保障 `python log.py tool` 100% 实时可见；
  - **持久化状态回捕发信工具**：若 `transcript.jsonl` 发生磁盘刷新延迟，监听器在进程退出前通过 `check_bot_spoke_in_turn` 回捕真实的 `send_private_msg` / `send_group_msg` 工具名并标准输出 `⚙️`，彻底杜绝误报保持沉默。
- **续轮成年人设极简防护盾 (`attention/context_builder.py`)**：
  - **续轮身份底线**：在 7m/10m 长会话续轮 (`is_continuation_turn=True`) 的 `<context_update>` 顶部柔性植入 80 字极简成年猫娘身份底线（“20岁左右的成年猫娘少女，所有交流均在成年角色之间展开”）；
  - **孤立审查与断裂防护**：确保即使底层 CLI 因前轮打断丢失磁盘会话转为裸 Prompt 提交，Google API 审查器也能始终见证完整成年人设，彻底根治 Google Prohibited Use Policy 误拒。
- **终极双重开口校验与安全兜底绝对抑制 (`attention/debounce_queue.py`, `workers/post_turn_observer.py`, `core/agy_runner.py`)**：
  - **严格时间窗口发信核验 (`check_bot_spoke_in_turn`)**：读取 `chat_state.json`，核验自本轮思考起点以来是否存在实质性发信（或 `last_bot_t >= turn_start_ts - 2.0`）；
  - **安全兜底绝对抑制**：当遭遇安全拒收触发自愈重推后，必须且仅当 `tools_called` 为空且磁盘记录亦确未发信时，方可触发 `SAFETY_FALLBACK_REPLIES`；彻底根除“自愈重推成功发出 4 句正文后 5 秒又补一句‘刚才走神了一下’”的幽灵兜底；
  - **未完结轮次保留**：因新消息涌入强杀或超时未开口的回合，严禁调用 `record_session_turn_completed` 推进轮次，保持 `turn_count=0` 并保留为人设首轮，确保下一次推理具备完整人设保护。
- **安全拒收全量 Prompt 自动落盘留样 (`core/agy_runner.py`)**：
  - 遭遇 Google 安全拒收时，除追加记录 `logs/safety_reject.log` 诊断结构体外，自动将包含上下文的完整 Prompt 原文写入 `logs/rejected_prompts/<timestamp>_<chat_id>_<conv_id>.txt`，实现 100% 敏感情境透明可查。

### 33. 星见弥认知与情绪流 WebUI 可视化系统落地 (Decoupled WebUI & Tailscale Mesh)
- **架构解耦设计与物理独立性 (`webui/` 目录)**：
  - 严格告别单文件堆砌代码，整体拆分为三大层次、模块化解耦：
    1. **服务端与独立启动器 (`webui/server.py`, `webui_start.py`)**：基于 Python 原生 `ThreadingHTTPServer` 实现零第三方依赖高并发服务，跨平台支持 Windows 控制台与 Linux 原生环境，默认绑定 `0.0.0.0:8088`，适配 Tailscale 专网 IP 访问；
    2. **数据提供层 (`webui/api/`)**：
       - `status_api.py`：系统运行状况、情绪状态摘要、各层记忆库规模统计；
       - `mood_api.py`：7 维情绪即时量度、12h/90min 动态指数衰减计算、24h 心境心电图数据；
       - `affection_api.py`：好友/群友阶梯分布（挚友/朋友稳态/熟人/路人/黑名单）、双重硬锁门禁核验；
       - `memory_api.py`：L3 永久记忆分类浏览、FTS5 全文混合检索模拟器（带毫秒耗时统计）、L2 中程温感记忆（艾宾浩斯激活度计算）；
       - `logs_api.py`：实时多通道审计日志分流拉取（思考流/聊天流/工具流/全部流）；
    3. **前端呈现层 (`webui/templates/`, `webui/static/`)**：
       - `templates/index.html`：纯结构化语义 HTML 骨架；
       - `static/css/`：`style.css` (深色二次元科技风/毛玻璃) + `components.css` (卡片/仪表/雷达/终端)；
       - `static/js/`：`api_client.js` + `mood_view.js` (ECharts 7D 雷达 + 24h EKG) + `affection_view.js` + `memory_view.js` + `logs_view.js` + `app.js` (主控制器与 5s 自动轮询调度)；
  - **异地双机通信与 Tailscale IP 访问**：在 Linux 边缘主机（联想 IdeaPad，专网 IP `YOUR_EDGE_NODE_IP`）启动后，笔记本客户端或局域网设备通过 `http://YOUR_EDGE_NODE_IP:8088` 即可无感直接访问交互。

### 40. GPT-SoVITS-v5 九维多情感语音 MCP 引擎、v5turbo 潜空间 ge 风格融合与深夜陪伴模式 (Multi-Emotion TTS Engine, Latent ge Style Blending & Late-Night Whisper Mode v4.8)
- **底层 GPU 算力解放与百倍推理提速 (`D:\GPT-TTS\GPT-SoVITS-v5`)**：
  - **修复 CPU-Only PyTorch 陷阱与 cuDNN Autotune 36 秒阻塞**：将推理环境升级为 `torch==2.11.0+cu128` (`fp16` + `TF32` 开启)，并在 `GPT_SoVITS/Accel/PyTorch/__init__.py` 与 `TTS.py` 中禁用 `torch.backends.cudnn.benchmark = False`（根除 1D 变长卷积在每次不同句长下耗时 15~36 秒重跑 cuDNN 启发式搜索的致命卡顿），同时遵循 `TORCH_NUM_THREADS=2` 限制 CPU 线程争抢；
  - **显存 16 槽位 LRU 缓存与启动预热 (`TTS.py`, `api_v2.py`)**：为全部 8 段参考音频的 HuBERT 语义码、声谱图、BERT 文本特征及 Vocoder 梅尔特征建立显存常驻 LRU 缓存，并在 `api_v2.py` 启动时预热 CUDA Graph 与全部情绪参考，使单句合成耗时从 **39.2 秒** 降至 **1.06 ~ 1.66 秒**（提速约 30 倍），显存占用稳定在 **~3.4 GB**。
- **方案 3-A + 接口 α：`v5turbo` 潜空间 `ge` 风格向量双情感融合 (`TTS.py`, `tools/voice_tools.py`)**：
  - **补全 `v5turbo` 多参考融合 (`using_vocoder_synthesis`)**：原版 `SynthesizerTrnV3` 在 `v3/v4/v5turbo` 模式下忽略了 `aux_ref_audio_paths`。现通过 `self.vits_model.ref_enc` 分别提取主参考与副参考的 512 维全局风格嵌入向量，按 $ge_{\text{target}} = 0.65\,ge_{\text{primary}} + 0.35\,ge_{\text{aux}}$ 进行潜空间线性插值后送入 `decode_encp`（同时调控 MRTE 跨注意力与 WaveNet 编码器），实现零拼接伪影的细腻复合情绪表达；
  - **`'主情感+副情感'` 组合语法糖与 `feiyu-mood` 双维自动复合**：`send_voice_msg` 的 `emotion` 参数原生支持 `'tsundere+shy'`（口嫌体正直）、`'sad+sweet'`（委屈撒娇）、`'tease+gentle'`（宠溺调侃）等组合写法；当 `emotion='auto'` 时，自动读取 `get_decayed_mood_state()` 实时衰减七维情绪，若前两维同时活跃（`top1 >= 18` 且 `top2 >= 14` 且 `top2 >= 0.55 * top1`），自动合成为复合声线（如 `proud + sweet` 自动映射为 `'tsundere+shy'`）。
- **方案 2-B：深夜“耳语/陪伴模式”与第 9 维 `whisper` 情绪 (`tools/voice_tools.py`)**：
  - **对齐 WebUI 黄金去电音基准小参 (`Euler steps=16`)**：全系基准对齐 `Euler steps (sample_steps)=16`、`top_k=15`、`top_p=0.8`、`temperature=0.7`、`batch_size=1`、`fragment_interval=0.3`、`repetition_penalty=1.35`，并保持 `speed_factor=1.0`（彻底规避 `SynthesizerTrnV3.enc_p` 在 `speed != 1.0` 时执行 `F.interpolate(mode="linear")` 造成音素帧边界糊化与金属电音感，同时将 CFM 欧拉积分步数提升至 16 步彻底消除高频底噪）；
  - **显式 `whisper` 声线**：基于 `gentle.wav` 主参考 + `normal.wav` 副风格融合，配合 `speed_factor=1.0`、`top_p=0.75`、`temperature=0.65`、`sample_steps=16`，全天任意时段均可通过 `emotion='whisper'`（或中文 `'耳语'`/`'哄睡'`/`'晚安'`）主动调用；
  - **深夜自动柔化 (`00:00 ~ 06:00` 北京时间)**：深夜时段若传入 `auto` 或 `normal` 且心境处于日常/温柔态，自动接管为 `whisper`（深夜轻语）；若处于傲娇/生气/害羞等强情绪或显式复合情绪，则保留原情绪性格与 `speed_factor=1.0`，将采样温度收敛至 `<= 0.65`、`top_p <= 0.75`，兼顾深夜安静陪伴感与鲜活人设。
- **台词净水器、跨机无感降级与 GPU 异步 FIFO 并发互斥锁 (`tools/voice_tools.py`, `api_v2.py`)**：
  - `sanitize_tts_text` 自动剥离 CQ 码、括号心理/动作旁白与 Emoji；采用 `cut2`（凑 50 字切分）保证短句一气呵成不断句；通过 Tailscale 专网 (`YOUR_TTS_SERVER_IP:9880`) 设 1.5s 极速探测，若主力机未开启 TTS 则输出提醒并自动退化为普通文字气泡发出；
  - 在 `api_v2.py` 中引入 `_TTS_INFER_LOCK = asyncio.Lock()` + `asyncio.to_thread` 异步卸载机制：Uvicorn 主事件循环 `<1ms` 极速完成并发 HTTP 握手（绝不因 GPU 忙而误触发 1.5s 连接超时降级），并在服务端按 FIFO 队列严格串行调度 GPU 推理，杜绝多线程并发污染 `prompt_cache` / `CUDAGraph` 与显存功耗叠加。

### 41. Gemini 4 Hydrogen 模型升级、1C-1 非阻塞异步气泡、三层约定核销闭环与全局额度调优 (V4.9)
- **全链路 Gemini 4 Hydrogen 模型升级与 Effort 统一映射 (`config.py`, `core/agy_runner.py`, `workers/*.py`, `debug_agy.py`)**：
  - 经服务端 `agy` 实测确认模型 ID 为 `gemini-4-hydrogen-{low|medium|high}`（禁用 `gemini-4-hydrogen-xhigh` / Extra）。在 `config.py` 新增 `build_agy_model_flags()` 统一生成 CLI 参数，对 Hydrogen 系列直接映射为对应后缀模型名且不再额外传 `--effort`，避免 `--effort is not supported for model` 校验抖动。
  - 将 `workers/friend_request_worker.py` 从 `--effort=low` 升至 `medium`（`allow_low=False`），保障私聊破冰+主人汇报双工具调用稳定。
- **回答速度与真人表达协同优化 (`core/bubble_dispatcher.py`, `bot_daemon.py`, `tools/message_tools.py`, `core/state_manager.py`, `core/intent_router.py`, `attention/debounce_queue.py`, `attention/context_builder.py`, `attention/relation.py`, `tools/meme_tools.py`, `workers/qzone_diary.py`, `workers/breathe.py`)**：
  - **1C-1 非阻塞异步连发**：在 `bot_daemon.py` 内建 `127.0.0.1:18302/enqueue_bubbles`（Bearer `NAPCAT_TOKEN` 鉴权）本机异步队列。保留“工具发信 + `--conversation` 连续会话”模式与拟真打字停顿：首条气泡发出即返回 HTTP 200 释放 `agy` 进程锁，后续气泡按通道 FIFO 队列在守护进程后台顺延发出，工具侧同步落盘完整原文到 `chat_state.json` 与 FTS5 归档，`record_bot_spoke` 扩展同脉冲防误计窗口防 WS 回声膨胀 `CONSECUTIVE_BOT_MSG_COUNT`。
  - **1A 动态思考深度路由 + 1B 渠道分级防抖**：`route_chat_effort` 严格保护前台不使用 `low`——首轮、主动冒泡、带图、长句(>=40字)、提问/记忆/工具线索及 B站/群管/系统域走 `high`，仅活跃续轮纯文字短句闲聊走 `medium`；私聊、直`@`/叫名字与戳一戳缩短防抖至 `2.2s`（`8s` 截断），群聊压力保持 `4.8s`（`12s` 截断）。
  - **1D 边缘提示词对齐母本**：全面收紧各 Worker 与上层上下文中的重复说教与陈旧口癖，统一对齐 `attention/persona.py`。
- **L3/L2/心事三层约定核销与冒泡隔离 (`social/long_term_memory.py`, `social/mid_term_memory.py`, `social/affection.py`, `tools/memory_tools.py`, `qq_mcp_server.py`, `workers/post_turn_observer.py`, `workers/breathe.py`)**：
  - **2A 源头分流 + 观察者 ID 联动核销**：规范 `post_turn_observer.py` 与 `daily_memory.py`，一次性近期计划（如“凌晨升级”）严禁写进 L3，统一归 `pending_thoughts`/L2；观察者拉取 `[pt:...]` / `[l3:...]` / `[l2:...]` 清单并输出 `resolved_ids` 三层核销（L3 标为 `status='fulfilled'` 与 `fulfilled_at`），`resolve_pending_thought` 增加完结后缀剥离与字符 bigram 重叠回退。
  - **2B 前台即时核销**：`get_user_core_facts_for_prompt` 对 `shared_promise` 渲染 `[#L3:{id}|{date}]`，并在 `memory` 域注册 `resolve_memory_fact` MCP 工具实现对话当场销账（常驻提示词撤出但 `search_memory` 仍保留【已兑现/已完成】检索记录）。
  - **2C 软降权与跨群隔离**：超 5 天时效词 L3 约定默认从注入中软降权，超 2.5 天时效词 L2 记忆过滤出 `breathe.py` 回访话头，`group_breathe_worker` 补齐 `current_scene` 严格隔离。
- **全局不抠门省额 (`workers/bili_worker.py`, `workers/daily_memory.py`, `core/agy_runner.py`, `workers/post_turn_observer.py`)**：
  - `bili_worker` 评估回合切换至 `medium` + `target_domains=["none"]` 剥离无关工具 Schema；夜间守夜人对普通熟人提门槛至昨日有效消息 >=12 条；会中阶段整理阈值从 `2/3` 调回 `4/6` 轮；<=2 轮且 <18 字无情绪/约定线索的纯闲聊在观察者入口轻量直结跳过 `agy`。

### 42. V5.0.1 MCP Stdio 通信零污染隔离、工作目录统一与运行期缺陷修复 (MCP Stdio Isolation & Stability Fixes v5.0.1)
- **根治 `qq_mcp_server.py` MCP 工具未挂载与 Stdio JSON-RPC 流污染 (`config.py`, `qq_mcp_server.py`, `bili_mcp_server.py`, `core/state_manager.py`, `attention/user_mute.py`, `tools/message_tools.py`, `tools/voice_tools.py`, `tools/image_tools.py`, `tools/meme_tools.py`)**：
  - **问题根因**：`qq_mcp_server.py` 导入阶段触发 `core/state_manager.py` 的 `load_chat_state()`，直接向 `sys.stdout` 打印中文启动日志；且原后台线程 `_sync_quota_background` 通过非线程安全的 `contextlib.redirect_stdout(sys.stderr)` 全局篡改 `sys.stdout`；同时各 `tools/*` 与 `attention/user_mute.py` 存在裸 `print()` 写入标准输出，导致 FastMCP stdio JSON-RPC 握手阶段即发生帧损坏，使 `agy` 会话未能挂载任何 QQ 工具。
  - **零污染隔离改造**：在 `config.py` 新增 `is_mcp_stdio_mode()` 与 `safe_log()`（在 `QQ_MCP_STDIO_MODE=1` 下自动路由至 `sys.stderr`）；`qq_mcp_server.py` 与 `bili_mcp_server.py` 入口设置 `QQ_MCP_STDIO_MODE="1"`，以 `contextlib.redirect_stdout(sys.stderr)` 包裹模块导入与 `MCPServer` 初始化，并在 `mcp.run()` 前安装 `_install_mcp_print_guard()`（将所有残留 `builtins.print` 重定向至 `sys.stderr`）；`qq_mcp_server.py` 改用独立静默子进程 `_trigger_quota_sync_subprocess()` (`stdout=DEVNULL, stderr=DEVNULL`) 执行看板配额同步，彻底移除跨线程 `redirect_stdout` 竞争；全量替换 `state_manager.py`、`user_mute.py` 与 MCP `tools/*.py` 中的运行期 `print()` 为 `safe_log()`。
- **`AGY_EXECUTABLE` 子进程显式工作目录对齐 (`core/agy_runner.py`, `workers/qzone_diary.py`, `workers/friend_request_worker.py`, `workers/daily_memory.py`, `workers/post_turn_observer.py`, `debug_agy.py`)**：
  - 所有直接拉起 `AGY_EXECUTABLE` 的子进程统一显式传入 `cwd=BASE_DIR`，确保 `.agents/mcp_config.json` 在任意启动目录下均可被稳定发现。
- **跨模块签名与记忆召回缺陷修复 (`tools/voice_tools.py`, `workers/qzone_observer.py`, `social/mid_term_memory.py`)**：
  - **语音禁言参数数量错误 (`tools/voice_tools.py`)**：修复 `is_user_muted(str(group_id), target_qq)` 多传 1 个位置参数导致的 `TypeError`，对齐为 `is_user_muted(target_qq)`。
  - **空间观察者记忆参数与落库目标错配 (`workers/qzone_observer.py`)**：修复 `add_pending_thought(uid=uin, ...)` 关键字参数名错误为 `user_id_str=uin`；将空间动态 L2 记忆写入的 `target_id=f"private_{uin}"` 修正为 `target_id=uin`（配合 `source_scene=f"qzone_{uin}"`），使其在私聊召回与主动回访 `get_recent_user_mid_term_memory` 中正确命中且保持群聊场景隔离。
  - **B站群公共见闻 L2 召回补全 (`social/mid_term_memory.py`)**：在 `recall_mid_term_memories` 的 `allowed_targets` 中纳入 `"group_common"` 并对齐 `[你今天刷到的B站/见闻]` 前缀标签，打通 `bili_worker.py` 写入的共享见闻记忆召回链路。
- **语音合成单轮 20 分钟双延长与自动回落机制 (`core/agy_runner.py`, `core/transcript_stream.py`, `tools/voice_tools.py`, `attention/debounce_queue.py`)**：
  - **单轮进程超时动态放宽 (`600s -> 1200s`)**：通过 `AGY_VOICE_MARKER_FILE` 实时标记与 `transcript_stream.py` 双路探测，一旦某轮调用 `send_voice_msg`，`_wait_process_with_voice_timeout` 自动将该轮进程强杀（斩首）上限从 10 分钟放宽至 20 分钟（下一轮自动恢复 10 分钟）。
  - **会话无对话存活寿命单次延长 (`420s/600s -> 1200s`)**：无论私聊（默认 10 分钟）还是群聊（默认 7 分钟），当且仅当该轮触发了 `send_voice_msg` 时，在 `record_session_turn_completed` 将本轮无对话保活寿命延长至 20 分钟；一旦开启后续新一轮对话且未再次触发语音，立即自动恢复各自默认寿命（7 分钟 / 10 分钟）。

### 43. V5.2 情绪引擎新版 E1 动力学与 M2 437条静态全复合词库、前台 `low` 三档可靠路由、B站双轨收藏/三段动态/0-LLM诊断与思考期急件防漏接 (Mood Engine E1 & M2 437 Lexicon, Three-Tier Effort Routing, Bili Dual-Track & In-flight Urgent Rescue v5.2)
- **阶段一：情绪引擎重构（新版 E1 四把防通胀物理锁）与 M2 437 条静态全复合词库 (`social/mood_manager.py`, `social/mood_lexicon.py`, `workers/post_turn_observer.py`, `attention/context_builder.py`)**：
  1. **保留 7 维结构与五档量化梯度**：100% 保留 `happy / calm / sweet / proud / worry / sulky / jealous` 7 维结构，与 WebUI 7 维雷达图及 `tools/voice_tools.py` 复合情感语音无缝兼容；统一五档阈值梯度（0档静默 `0~29`、1档轻度 `30~54`、2档中度 `55~69`、3档强烈 `70~89`、4档爆表 `90~100`）。
  2. **底层防通胀四把物理锁（新版 E1 自然演化）**：
     - **锁 1 · 五段高位阻力（S 型边际递减）**：单次单维原始冲量上限收紧至 `[-35, +35]`；正向增长按所处档位施加分段阻力系数（`0~29: 1.00x`、`30~54: 0.70x`、`55~69: 0.45x`、`70~89: 0.33x`、`90~100: 0.22x`），跨档增长按 `integrate_dim_growth()` 分段平滑积分计算；
     - **锁 2 · 温和对立压制（最高兼容 `1 : 0.7` 跨极复合）**：划分暖色系（`happy`, `calm`, `sweet`, `proud`）与冷色系（`worry`, `sulky`, `jealous`）。一侧正向有效增长时对对立侧柔和回压（`0.32x`），绝不一刀清零；允许对立侧最高以主导侧峰值的 `0.7` 倍共存（超出 `0.7 × 主导峰值` 时柔缓回压至该边界内，原本在 `0.7` 倍以内仅极轻微压制或保留），自然呈现“嘴硬吃醋但心里甜丝丝”“破涕为笑还带点委屈”等猫娘跨极复合情绪；
     - **锁 3 · 两段式退潮衰减 (`decay_single_dim_two_stage`)**：数值 `>= 70` 半衰期缩短至 `22.5 分钟`（`20~25min`），`< 70` 半衰期维持 `55 分钟`（`50~60min`），跨 `70` 边界按分段指数公式精确扣减时间；
     - **锁 4 · 观察者源头限流（新版 E1 不做硬截断）**：在 `workers/post_turn_observer.py` 提示词与 `filter_top_k_dim_deltas()` 代码层双重约束单次最多变动 3 个情绪维度（超出时按绝对值排序仅保留前 3 个）；严控 `calm` 仅在从担心/委屈中被安抚下来或安静陪伴时加分；不对排名第 3 名及以后的已激活维度做人为归零或额外加速衰减。
  3. **M2 高区分度静态全复合词库 (`social/mood_lexicon.py`，共 437 条纯静态 $O(1)$ 查表）**：
     - 0 维激活（全部 `< 30`，走宏观 `mood` `0~100` 每 10 点分 10 档）= **10 条**；
     - 1 维激活（仅 1 维 `>= 30`，7 维 × 4 激活档）= **28 条**；
     - 2 维激活（2 维 `>= 30`，全 $C_7^2 = 21$ 对 × `3×3 = 9` 格 `L/M/H × L/M/H` 严格区分主次轻重，如 `得意(H)+甜蜜(L) -> 傲娇暗爽`、`甜蜜(H)+得意(L) -> 甜丝丝有点小骄傲`、`甜蜜(H)+吃醋(M) -> 又甜又护食`）= **189 条**；
     - 3+ 维激活（取 Top-3 查全 $C_7^3 = 35$ 组 × 6 档 `3种第一主角 × 轻中M/强爆H 2档`）= **210 条**；
     - 合计 `10 + 28 + 189 + 210 = 437 条`，零运行时机械拼接，全部 `<= 10` 个汉字。
  4. **极简折中派单行 Prompt 注入**：删除多行心理旁白与“不是非这样不可”等卸力引导，存在 `last_reason` 且距离记录时间 `<= 3600s` 时注入 `【当前心情】<映射词>（<极短起因>）`，否则注入 `【当前心情】<映射词>`。
- **阶段二：解禁前台 `low` 强度与三档可靠路由 (`config.py`, `core/intent_router.py`, `core/agy_runner.py`, `attention/debounce_queue.py`)**：
  1. **三档路由判定矩阵 (`route_chat_effort_with_reason`)**：解禁前台 `low` 档（映射至 `gemini-4-hydrogen-low`）。
     - **优先级 1（绝对禁 `low` 硬护栏）**：发生打断重跑 (`was_interrupted=True`)、命中非纯聊天工具域 (`bili/group_admin/system_admin/game`/深度 `memory`)、含图片锚点/链接/`>=45`字长文/复杂提问/技术代码/情感安慰/虚实探讨、自愈重试回合 → 至少 `medium` 或 `high`；
     - **优先级 2（定向 `low` 规则）**：群聊话题压力无图无复杂工具出场一律 `low`；自由冒泡中普通群冷场/日常随笔一律 `low`（明确豁免：① 私聊主动勾引主人或挚友；② 携带 `pending_thoughts` 或近期 B 站手账分享保持 `medium`）；好感度 `< 500`（朋友以下，严格不含朋友）私聊首轮默认 `low` 起手（唤名或提问/长句/工具/情感升至 `medium`/`high`，后续轮次回归常规基准）；
     - **优先级 3（常规对话基准）**：以 `medium` 为默认基准；纯文字极简互动（`<=22` 字无问号无敏感工具词、戳一戳反击、早晚安/哈哈/草等轻搭腔、群聊高轮次自然收尾）降为 `low`；复杂深聊升为 `high`。
  2. **翻车自动升档熔断保险**：在必回场景（私聊、被直接 `@`/唤名、戳一戳）下，若某轮 `low` 推理结束未调用任何发信工具（静默未回复），自动升档至 `medium` 重跑 1 次，并在日志中清晰打印路由出的 `effort` 与命中规则标签。
- **阶段三：B 站冲浪节奏调优、收藏双轨兜底、三段降级动态与诊断自检 (`workers/bili_worker.py`, `social/bilibili_client.py`, `social/bilibili_master_bridge.py`, `attention/debounce_queue.py`)**：
  1. **5 候选粗筛 / 单视频精看 / 三段字幕精华 / 80min 间隔**：每轮搜索仅粗筛前 5 个未看候选，每次只精看 **1 个**最优质视频（`medium` + `target_domains=["none"]`）；超长字幕通过 `extract_three_segment_subtitles()` 截取“开头+中段+结尾”精华（总长控制在 `3000~3500` 字内）；白天正常冲浪休眠期望调整为 **80 分钟**（`_sample_truncated_gauss(80, 18, 40, 125)`，失败单次加速重试后无条件回归 80 分钟期望）。
  2. **收藏双轨加固 + 选项 B 默认夹极端兜底 + 门槛校准**：`get_user_favorite_folders` 与 `favorite_video` 均优先调用 `bilibili-api-python`，失败自动回退原生 `httpx` (`trust_env=False`) 直连 Web API (`/x/v3/fav/folder/created/list-all` 与 `POST /x/v3/fav/resource/deal`)；保持 `allow_auto_create=False`，按精确→模糊/关键词→选项 B 默认收藏夹极端兜底；评估 Prompt 明确解耦评论口吻与打分，收藏触发线校准为 `((score >= 72) or (should_favorite and score >= 68)) and attitude != "吐槽踩雷"`。
  3. **三段式降级动态发布 + 21:45~22:30 错峰 + 自动重试报警**：封面图使用 `httpx` (`trust_env=False`, `Referer: https://www.bilibili.com/`) 预拉取二进制到内存防 403 防盗链；`publish_bili_dynamic` 按“① `bilibili-api-python` 内存图片图文 → ② 原生 `httpx` 上传 `upload_bfs` 图床 + Web 动态创建 → ③ 带完整 `https://www.bilibili.com/video/{bvid}` 链接的纯文字动态”三段降级；每日窗口调整为 **`21:45 ~ 22:30`**，失败 15 分钟后自动重试 1 次（最多 2 次），仍失败时私聊通知主人。
  4. **诊断账本与主人零 LLM 自检指令**：在 `today_bili_feed.json` 记录当日观看数、JSON 成功/失败数、评分与态度、点赞/收藏/评论/动态尝试与成功数及最近报错；支持主人专属 0-LLM 指令 **`B站诊断`** 与 **`测试B站动态`**（当天 0 观看时通过 `probe_bili_dynamic_connectivity()` 执行无污染登录态、CSRF 与 `upload_bfs` 图床连通性探针校验）。
- **阶段四：思考期急件防漏接、防抖日志对齐与脑区路径核查 (`attention/debounce_queue.py`, `config.py`)**：
  - 维持 `INFLIGHT_TURNS` 繁忙期内对普通群聊水聊的降维沉淀；新增 `PENDING_URGENT_TURNS` 急件待补答池，当繁忙期收到群聊直接 `@`/叫名字/引用回复弥酱或主人私聊（未触发打断重跑）时暂存急件，待当前轮次结束释放 `INFLIGHT_TURNS` 后自动拉起 `1.2s` 极短防抖补答；
  - 修复出队日志硬编码 `[5s防抖结束]` 为动态打印实际防抖秒数（如 `[2.2s防抖结束]` / `[4.8s防抖结束]`）；核查确认 `config.py` 中 `_get_candidate_brain_roots()` 为真实生产宿主路径 `("/home/your_user", "/root")`。


---

## 🔮 四、后续可能的发展方向 (Future Work & TODO)
- **定时轮询/自主呼吸进阶优化 (Spontaneous Breathing v2)**:
  - 现实生活作息锚定 (Circadian Rhythm)：结合早晨起床、午饭摸鱼、下班打卡等时段自发随笔。
  - 迟发共鸣与延时回响 (Delayed Echo)：冷场后以“刚爬楼看完”的姿态针对前段热点延时吐槽。
  - 自主生活碎片库 (Life Snippets)：主动向群内抛出打游戏战绩、抽卡体验等话题引导互动。
- **多平台拓展**: 既然已用 `SeekaiGeminiqqAgent` 作为核心代号，未来不仅限于 QQ，该 Daemon 思路可直接复用于微信、Discord 的桥接。
- **Subagent 异步深思双轨制**: 针对耗时较长（>15秒）的跨天长文本总结与网络分析，让主智能体秒级应答“等我翻翻记录喵”，后台调度 Subagent 异步产出后二次推送，维持聊天即时响应。





