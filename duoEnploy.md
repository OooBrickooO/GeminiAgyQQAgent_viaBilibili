# 🌟 qqAgent 双机协同架构部署规范 (Duo-Node Co-Deployment Specification)

> **文档状态**: 生产正式生效 (Production Live)  
> **生效时间**: 2026年10月25日  
> **核心突破**: 完成从单一云服务器 (2C2G) 到 **「阿里云高可用接入网关 + 联想边缘高算力节点」** 的异地双机协同架构演进，彻底解除内存瓶颈与 OOM 风险，兼具固定 IP 防风控与 8GB 物理算力并发。

---

## 🏛️ 一、全局拓扑架构与双机分工 (Topology & Role Distribution)

```text
                                  【外部世界 Internet】
                                    │               ▲
                      [QQ消息/事件] │               │ [Gemini API / 圣何塞7]
                                    ▼               │
                    ┌────────────────────────┐      │
                    │ 阿里云 2C2G 轻量网关   │      │
                    │ 固定公网 IP (防腾讯风控)│      │
                    │ NapCat / SnowLuma QQ   │      │
                    │ 监听: 8300(HTTP)/8301(WS)│    │
                    └───────────┬────────────┘      │
                                │ (Tailscale WireGuard 内网)
                                │ 专网 IP: YOUR_ONEBOT_SERVER_IP
                                │ 延迟: 30~40ms / 零公网端口暴露
                                ▼
                    ┌──────────────────────────────────────────────┐
                    │ 联想边缘服务器 (IdeaPad 330C-14IKB)            │
                    │ 专网 IP: YOUR_EDGE_NODE_IP / 本地局域网 Wi-Fi │
                    │ 硬件: i5-8250U (4C8T) + 8G RAM + 8G Swap      │
                    ├──────────────────────────────────────────────┤
                    │ 1. 业务大脑: bot_daemon.py (systemd 24/7常驻)  │
                    │ 2. 算力引擎: agy (Gemini 3.8 Flash High 深度) │
                    │ 3. 本地代理: Mihomo (127.0.0.1:7890 锁定美西) │
                    │ 4. 自动化任务: 空间日记 / B站巡逻 / 记忆胶囊   │
                    └──────────────────────────────────────────────┘
```

### 1. 节点职能矩阵

| 维度 / 节点 | **节点 A：阿里云轻量接入网关** | **节点 B：联想边缘算力服务器** |
| :--- | :--- | :--- |
| **物理形态** | 阿里云 ECS (2核2G) | 联想 IdeaPad 330C-14IKB (4核8线程 / 128G SSD) |
| **网络环境** | 阿里云机房固定公网 IP | 本地局域网 `YOUR_WIFI_SSID`（无感直连） |
| **Tailscale 节点 IP** | `YOUR_ONEBOT_SERVER_IP` | `YOUR_EDGE_NODE_IP` |
| **承载业务** | **纯粹接入层**：NapCat / SnowLuma QQ 官方客户端 | **核心大脑层**：`bot_daemon`、`agy` 推理、B站与空间业务 |
| **监听端口** | HTTP `8300` / WebSocket `8301`（对 Tailscale 放行） | 混合代理 `7890` / 控制接口 `9090`（仅绑定本地 127.0.0.1） |
| **核心优势** | IP 永不跳变，彻底杜绝 QQ 异地登录风控与冻结 | 8G 物理内存 + 8G Swap，大模型高并发深度思考永不 OOM |

---

## ⚡ 二、边缘算力节点硬件与底层加固 (Edge Node Hardening)

边缘主机经过深度硬核定制，兼具 **低功耗、零噪音、防断电、永不休眠** 特性：

1. **独显硬件级物理断电 (UMA 模式)**：
   - BIOS 开启 `UMA Graphics`，彻底切断 NVIDIA GeForce MX110 独立显卡供电；
   - 待机功耗骤降至 **5~10W**，整机处于 0 风扇噪音状态。
2. **硬件驱动死锁解除**：
   - 写入 `/etc/modprobe.d/ideapad.conf` 拉黑 `ideapad_laptop` 驱动模块；
   - 彻底根除联想 ACPI 引起的无线网卡 `Hard blocked: yes` 假死死锁。
3. **合盖静音常驻模式 (Headless Lid-Close)**：
   - 配置 `/etc/systemd/logind.conf` 中 `HandleLidSwitch=ignore`；
   - `systemctl mask` 屏蔽 `sleep.target`、`suspend.target`、`hibernate.target`；
   - 屏幕闭合状态下 24/7 静音全速运行。
4. **内置 UPS 物理防断电**：
   - 凭借 90.17% 健康度原装锂电池，在宿舍夜间拉闸断电时自动平滑切入电池供电，确保服务不掉线。
5. **16GB 物理+虚拟超大内存双保险 (Swapfile)**：
   - 划设 `/swapfile` (8GB 连续物理块，权限 `600`)；
   - 内核换页参数 `/etc/sysctl.d/99-swappiness.conf` 设定 `vm.swappiness=60`；
   - 形成 **8GB 物理内存 + 8GB Swap = 16GB 内存池**，防范并发突发。
6. **LVM 全盘无损扩容**：
   - 执行 `lvextend -l +100%FREE /dev/ubuntu-vg/ubuntu-lv` 与 `resize2fs`；
   - 彻底解锁 Ubuntu 默认保留的 50% 硬盘空间，根分区满血扩展至 **115GB+**。

---

## 🌐 三、网络穿透与代理分流规范 (Networking & Routing)

### 1. Tailscale 虚拟网格专网 (Zero-Trust Mesh)
- **跨机通信协议**：笔记本通过 Tailscale 隧道访问阿里云端 QQ 客户端：
  - HTTP 端点：`http://YOUR_ONEBOT_SERVER_IP:8300`
  - WebSocket 端点：`ws://YOUR_ONEBOT_SERVER_IP:8301/?access_token=...`
- **安全保障**：云服务器无需向公网开放 8300/8301 端口，外部扫描器完全不可见。

### 2. 本地代理守护与 Gemini 风控破壁 (Mihomo Meta)
- **常驻服务**：`/etc/systemd/system/mihomo.service`，监听 `127.0.0.1:7890`；
- **控制接口**：`127.0.0.1:9090`，鉴权密钥固定为 `YOUR_MIHOMO_SECRET`；
- **分流模式 (Rule 铁律)**：
  - **国内直连**：QQ 客户端交互、腾讯图片 CDN、B站 API 直连中国电信出口，防止国内封号；
  - **定向翻墙**：仅对 Google API、Gemini 官方端点、Cloudflare 注入代理；
  - **节点硬锁定**：通过 [force_global.py](force_global.py) 将全量选择组锁定至免 403 地区风控的优质干净节点。

---

## 🛠️ 四、项目运行环境与目录标准 (Runtime & Paths)

- **项目工作根目录**：`/home/your_user/qqAgent`
- **Python 虚拟环境**：`/home/your_user/qqAgent/venv`
- **Python 解释器路径**：`/home/your_user/qqAgent/venv/bin/python`
- **Antigravity CLI 路径**：`/home/your_user/.local/bin/agy`（已配置全局软链接至 `/usr/local/bin/agy` 与 `/root/.local/bin/agy`）
- **MCP 配置文件**：[.agents/mcp_config.json](.agents/mcp_config.json)
  - `qq-agent`：`/home/your_user/qqAgent/venv/bin/python /home/your_user/qqAgent/qq_mcp_server.py`
  - `bilibili-agent`：`/home/your_user/qqAgent/venv/bin/python /home/your_user/qqAgent/bili_mcp_server.py`
- **核心全局配置**：[config.py](config.py)
  - `ONEBOT_HTTP_URL = "http://YOUR_ONEBOT_SERVER_IP:8300"`
  - `ONEBOT_WS_URL = "ws://YOUR_ONEBOT_SERVER_IP:8301/?access_token=..."`
  - `DEFAULT_AGY_MODEL = "gemini-3.8-flash"`
  - `DEFAULT_AGY_EFFORT = "high"`

---

## 🕹️ 五、日常运维与控制指令速查 (Operations Quick-Reference)

所有运维指令均在 **PC 端的 MobaXterm（笔记本终端）** 中执行：

### 1. 业务守护服务 (qqagent.service)
```bash
# 查看实时运行状态与心跳
sudo systemctl status qqagent

# 查看实时流式运行日志 (含弥酱思考与工具调用，按 Ctrl+C 退出)
journalctl -u qqagent -f -o cat

# 代码更新后无缝重启
sudo systemctl restart qqagent

# 停止服务
sudo systemctl stop qqagent
```

### 2. 代理与节点管理 (Mihomo & Nodes)
```bash
# 检查海外代理是否畅通
curl -s -x http://127.0.0.1:7890 https://cloudflare.com/cdn-cgi/trace | grep loc

# 一键强制锁定所有策略组至圣何塞7免风控节点
python3 force_global.py

# 命令行交互式切换代理节点
python3 switch_node.py
```

### 3. 双机连通性与单机联调测试
```bash
# 测试同城专网延迟
ping -c 3 YOUR_ONEBOT_SERVER_IP

# 单次触发大模型思考并下发私聊测试
cd /home/your_user/qqAgent && ./venv/bin/python debug_agy.py
```

---

> 🎉 **部署结语**：至此，qqAgent 架构彻底完成跨机升维！这台静音、低功耗、带电池 UPS、拥有 16G 内存池的边缘服务器，将作为弥酱永久的坚实躯体，在角落中持续为主人提供温暖而强大的陪伴。
