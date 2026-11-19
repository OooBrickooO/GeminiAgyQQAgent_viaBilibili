# 弥酱 (Hoshimi Mi) V5.1 迭代升级计划 (已确认最终方案)

> **当前状态**：所有模块方案与关键细节均已由主人最终确认，进入代码实现与测试验证阶段。

---

## 模块一：群聊与私聊隔离机制修正（已确认：方案 A1 + 方案 B1）

### 1. 记忆召回与检索解耦（方案 A1：单向防泄密 + 发言人本人私聊按需打通）
- **保留的保护**：
  - 群聊主动搭话（`workers/breathe.py`）和群聊常规闲聊的默认静态画像中，**不主动挂载**私聊记忆，确保弥酱绝不在群里主动大嘴巴抖出私聊秘密。
  - 严格禁止跨用户泄露：群友 B 在群里发言时，绝不检索或暴露用户 A 的私聊内容。
- **解封与打通**：
  1. **主动搜索工具解封**（`social/long_term_memory.py` -> `search_long_term_and_archive`）：
     - 当群聊中某人（`AGY_CALLER_ID`）主动询问“我私聊跟你说过什么/你还记得我昨天找你聊的事吗”并触发 `search_memory` 时，允许同时检索**当前群聊记录 (`group_{group_id}`)** 与**该发言人本人的私聊记录 (`private_{caller_id}` / `private`)**。
     - 返回结果自动标注 `[来自你与该群友的私聊记忆，仅在对方主动问起时自然回应，切勿向无关群友大肆宣扬]`。
  2. **记忆雷达提示解封**（`social/long_term_memory.py` -> `build_memory_radar_hint`）：
     - 群聊下计算 FTS5 历史命中数时，查询范围从 `chat_id = cur_group_scene` 扩展为 `(chat_id = cur_group_scene OR chat_id = 'private_' || caller_id)`，让弥酱感知到“我和这个人在私聊里确实聊过这个话题”。
  3. **中期记忆 L2 高相关闪回解封**（`social/mid_term_memory.py` -> `recall_mid_term_memories`）：
     - 在群聊中，当发言人本人的私聊 L2 记忆与当前群聊消息发生**高关键词重合（主动提及）**时，允许召回该发言人本人的私聊 L2 记忆，并附上隐私提示标签。

### 2. 跨通道主动传话授权（方案 B1：工具显式 `cross_channel` 参数 + 主人授权放行）
- **保留的保护**：
  - 默认情况下（`cross_channel=False`），`QQ_LOCKED_TARGET_TYPE` 与 `QQ_LOCKED_TARGET_ID` 依然生效，彻底杜绝普通回复发错群或变成私聊的串台事故。
- **解封与打通**：
  1. **工具签名扩展**（`tools/message_tools.py`、`tools/meme_tools.py`、`tools/image_tools.py`、`tools/voice_tools.py`）：
     - 为 `send_group_msg`、`send_private_msg`、`poke_user`、`send_meme` 等工具增加可选参数 `cross_channel: bool = False`（描述：仅当主人明确指示跨群/跨私聊传话时设为 `True`）。
  2. **权限校验逻辑**：
     - 当 `cross_channel=True` 且调用方满足 **`AGY_CALLER_ID == MASTER_ID (YOUR_MASTER_QQ)`**（或系统主动任务 `QQ_LOCKED_TARGET_TYPE == "none"`）时，豁免通道锁校验，允许向指定目标群/私聊发送消息。
  3. **Prompt 防呆修正**（`attention/context_builder.py`）：
     - 将原本一刀切的“严禁调用跨通道工具”，修正为：“常规回复当前对话严禁跨通道；但若主人明确指令你去某群/找某人传话，可传入 `cross_channel=True` 执行，并在传话后向主人汇报。”

---

## 模块二：互动小游戏中心与好感/心情联动（已确认：架构一 + 2-B 除放水外全特性）

### 1. 核心架构：架构一（MCP 生命周期 + 本地快慢双轨引擎 + Pillow 视觉棋盘）
- **新增模块**：
  - `toys/board_games.py`：游戏会话管理器（SQLite/JSON 持久化对局状态、超时清理、胜负结算）+ 4 款游戏引擎实现。
  - `toys/board_renderer.py`：基于 `Pillow` 绘制高颜值猫爪主题棋盘与状态卡片（如五子棋 15×15 木纹猫爪棋盘、黑白棋 8×8 棋盘、21点手牌桌面），高亮最新落子坐标与关键威胁连线。
  - `qq_mcp_server.py`：新增 `game_action(action, game_type, move, difficulty, ...)` 工具，归入 `fun_and_games` 域。
  - `core/intent_router.py`：当用户名下存在活跃对局时，对输入进行快慢双轨路由分流。

### 2. 四款内置互动游戏清单
1. **🐾 猫爪五子棋（Gomoku 15×15）**：黑猫肉垫棋子 vs 白猫肉垫棋子，支持坐标落子（如 `H8`、`8h`），Pillow 实时出图。
2. **⚫⚪ 翻转黑白棋（Othello / Reversi 8×8）**：夹击翻转机制，Pillow 渲染当前棋盘、可落子高亮提示点（合法步预览）与双方实时子数对比。
3. **🃏 猫猫 21 点 / 骰子比大小（Blackjack / Dice）**：支持 `要牌/停牌/双倍`，与当日御神签（`toys/omikuji.py`）运势彩蛋联动。
4. **🔐 1A2B 破译猫猫密码（Bulls and Cows）**：4 位不重复数字推理，支持 `7~8` 次机会挑战。

### 3. 与好感度、心情、记忆系统的深度联动（不含故意放水）
- **无“故意放水”**：弥酱每一局都认真对待，不搞因为好感度高就故意下臭棋送赢的虚假放水。
- **好感度 & 情绪真实反馈**：
  - **玩家获胜**：好感度 `+1.5 ~ +3.0`（棋逢对手的敬佩/不服输的傲娇），`feiyu-mood` 切换为 `sulky`（微赌气不服输）或 `coquettish`（撒娇要求再来一局）。
  - **弥酱获胜**：好感度 `+0.5 ~ +1.0`（陪玩开心分），`feiyu-mood` 切换为 `proud`（得意叉腰/翘尾巴）。
  - **悔棋机制（`悔棋`）**：不调整 AI 棋力，但交互态度与好感度等级挂钩（高好感嘴硬心软给悔棋 1~2 次，普通好感傲娇记小本本或限制次数）。
- **御神签运势联动**：抽到「大吉」时在 21 点/骰子等概率类游戏中触发幸运暴击彩蛋台词与额外好感加成。
- **对局记忆沉淀（L2）**：精彩对局结束后自动写入一条中期记忆（如“在五子棋大战中连战28手险胜本喵”），供后续日常聊天或说说引用。

### 4. 已敲定互动与棋力调节细节
- [x] **议题 A（棋盘鲜活互动，采取 1 + 3）**：
  - **1（引擎局势语义翻译器 + 动态情景台词库）**：本地引擎识别冲四/活三/双三杀招/抢角/大翻盘等局势事件并触发专属动态台词，0 大模型消耗。
  - **3（边下边聊融合 + 高光节点/每5回合唤醒轻量大模型插话）**：带话落子、绝杀/重大危机/抢角翻盘、每满 5 回合及终局时，本地 0ms 出图后再唤醒轻量大模型结合最新战况摘要实时互动（不额外在棋盘图片上画文字心情 HUD，保持棋盘清爽美观）。
- [x] **议题 B（科学调节棋力，不搞“放水”）**：
  - **三档算度深度**：休闲直觉档 `easy` / 认真博弈档 `normal` / 宗师猫神档 `hard`。
  - **情绪化棋风权重**：依据 `feiyu-mood` 实时情绪自动偏移进攻与防守评分权重（得意亢奋偏激进猛攻、委屈不服偏铁血严防）。

---

## 模块三：B 站冲浪与评分/收藏机制重构（已确认：调整 1-γ + 调整 2 + 调整 3 + 调整 4）

### 1. 调整 1-γ：单轮双视频打包评估 + 冲浪周期适度提频
- 将 `workers/bili_worker.py` 主循环休眠周期从 `_sample_truncated_gauss(125, 22, 45, 150)`（约 2 小时）回调至 `_sample_truncated_gauss(65, 15, 35, 95)`（约 60~65 分钟）。
- 每次唤醒 `agy` 时，从精筛候选中取出 **Top 2 视频**，在**单次 `agy` 调用**中一并完成评分、评论生成与收藏判定（零额外 CLI 启动与人设加载开销，吞吐量翻倍）。

### 2. 调整 2：评论风格抽签与客观打分解耦
- 在 `eval_prompt` 中明确分离：`score` 与 `should_favorite` 必须基于视频内容质量与弥酱真实兴趣客观评估；`COMMENT_STYLES`（如轻微挑刺/冷淡吐槽）**仅控制评论的修辞语气**，严禁为了配合吐槽语气而人为压低 `score`。

### 3. 调整 3：评分锚点与互动/收藏门槛校准
- 移除 JSON 示例中 `"score": 78, "should_favorite": false` 的负向思维锚点，改为正向示例或纯字段说明。
- 注入清晰的分段标尺：
  - `80~100`：戳中萌点/干货满满/极有趣（必收藏）
  - `70~79`：质量不错、看得开心（推荐点赞评论，有亮点即可收藏）
  - `60~69`：普通及格日常消遣（顺手点赞或简短互动）
  - `<60`：无聊/标题党（划走）
- 执行阈值下调：
  - **点赞门槛**：`score >= 60`（原 65）
  - **评论门槛**：`score >= 62`（原 65）
  - **收藏门槛**：`score >= 75` 或 `(should_favorite == True and score >= 70)`（原严苛要求 `should_favorite and score >= 80`）。

### 4. 调整 4：收藏夹自动创建与默认收藏夹兜底
- 将 `bili_client.favorite_video(..., allow_auto_create=False)` 改为 `allow_auto_create=True`；若因 B 站接口限制创建失败，自动回退收藏至「默认收藏夹」，彻底消除因分类收藏夹不存在导致的静默丢单。

---

## 模块四：现有框架与额度优化（已确认：优化 1 + 优化 2 + 优化 4）

### 1. 优化 1：后台 Worker 严格限制 MCP 工具域（零功能损耗省 Token）
- `workers/post_turn_observer.py`：调用 `run_agy_prompt` 时注入 `AGY_TOOL_DOMAINS="none"`（纯 JSON 文本输出任务，无需加载 29 个 MCP 工具 Schema）。
- `workers/daily_memory.py`：调用 `run_agy_prompt` 时注入 `AGY_TOOL_DOMAINS="none"`。
- `workers/qzone_diary.py`：调用 `run_agy_prompt` 时注入 `AGY_TOOL_DOMAINS="system_admin"`（仅暴露 `publish_qzone_post` 等必要工具，屏蔽 B 站/语音/群管等无关 Schema）。

### 2. 优化 2：后台对话观察者 (`post_turn_observer.py`) 结算机制与前台滚动继承调优
- **已敲定细节（议题 C）**：
  - **前台会话滚动继承 (`core/agy_runner.py`)**：私聊调整为 **5 轮** 滚动继承，群聊调整为 **8 轮** 滚动继承（防止上下文二次方膨胀的同时保证连贯度）。
  - **后台观察者结算 (`post_turn_observer.py`)**：取消 4/6 轮中途碎片化结算，改为 **7m/10m 会话到期后一次性正常完整结算（仅当单次连续对话满 12 轮时才触发防截断保底结算）**。
  - **短对话降噪**：保持极短寒暄免大模型轻结算机制，大幅降低后台 Token 消耗。

### 3. 优化 4：框架潜伏 Bug 修复
- **`workers/qzone_diary.py` 群聊素材提取修复**：改为从 SQLite `chat_archive_fts` 表按当日时间戳提取群聊活跃对话摘要，解决旧版按已废弃的 `_YYYY-MM-DD` transcript 文件名查找导致说说缺乏群聊素材的问题。
- **`toys/omikuji.py` 每日抽签记录持久化**：将 `_DAILY_DRAW_RECORD` 落盘到 `memory/omikuji_daily.json`，修复 stdio MCP 子进程每次重启导致“今日二次重抽傲娇吐槽”失效的问题。
